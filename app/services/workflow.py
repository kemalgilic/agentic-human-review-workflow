"""Workflow orchestration (portfolio demo).

Task intake -> context validation -> risk assessment -> AI decision -> routing,
followed by exactly one of:

- AUTO_EXECUTE : simulated tool action runs, result is audited
- HUMAN_REVIEW : a review item is created and the task waits for a reviewer
- BLOCK        : the action is not executed, the block is audited

Fail-safe: the risk engine may tighten but never loosen the AI decision
(see routing.py). No failure is silent: every failure is written to the audit
trail and the exception queue, and is visible in the API response or raised.
"""

import time
from collections.abc import Callable

from app.human_review.decisions import DEFAULT_REVIEWER, approve_review, reject_review
from app.human_review.queue import ReviewQueue, get_review_queue
from app.schemas import (
    AIDecision,
    AuditEventType,
    DecisionType,
    RiskAssessment,
    Task,
    TaskCreate,
    TaskResponse,
    WorkflowStatus,
)
from app.services.audit import AuditLog, get_audit_log
from app.services.decision import DecisionProvider, make_decision
from app.services.exceptions import (
    ExceptionQueue,
    ExecutionFailedError,
    get_exception_queue,
)
from app.services.execution import (
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_ATTEMPTS,
    block_action,
    execute_action,
)
from app.services.risk import assess_risk
from app.services.routing import Route, route_task

ACTOR_API = "api"
ACTOR_RISK = "risk_engine"
ACTOR_DECISION = "ai_decision"
ACTOR_ROUTER = "router"
ACTOR_WORKFLOW = "workflow"


class WorkflowError(Exception):
    """An unexpected failure inside the workflow pipeline."""


class TaskValidationError(WorkflowError):
    """The task failed context validation."""


class WorkflowEngine:
    def __init__(
        self,
        *,
        audit: AuditLog | None = None,
        review_queue: ReviewQueue | None = None,
        exceptions: ExceptionQueue | None = None,
        provider: DecisionProvider | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay: float = DEFAULT_BASE_DELAY,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Explicit None checks: empty AuditLog / ReviewQueue / ExceptionQueue
        # are falsy (they define __len__), so "x or default" would be a bug.
        self.audit = get_audit_log() if audit is None else audit
        self.review_queue = get_review_queue() if review_queue is None else review_queue
        self.exceptions = get_exception_queue() if exceptions is None else exceptions
        self.provider = provider
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.sleep = sleep

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, request: TaskCreate) -> TaskResponse:
        task = Task.model_validate(request.model_dump())
        self.audit.record(
            task.task_id,
            AuditEventType.TASK_RECEIVED,
            ACTOR_API,
            details={
                "requested_action": task.requested_action.value,
                "target_system": task.target_system,
            },
        )
        self._validate_context(task)

        try:
            risk = assess_risk(task)
            self.audit.record(
                task.task_id,
                AuditEventType.RISK_ASSESSED,
                ACTOR_RISK,
                details={
                    "risk_level": risk.risk_level.value,
                    "risk_score": risk.risk_score,
                    "reasons": risk.reasons,
                },
            )
            decision = make_decision(task, risk, provider=self.provider)
            self.audit.record(
                task.task_id,
                AuditEventType.DECISION_CREATED,
                ACTOR_DECISION,
                decision=decision.decision,
                details={
                    "confidence": decision.confidence,
                    "explanation": decision.explanation,
                    "recommended_action": decision.recommended_action,
                },
            )
        except Exception as exc:
            raise self._pipeline_failure(task, "risk_and_decision", exc) from exc

        route = route_task(decision, risk)
        if route == Route.BLOCK:
            return self._block(task, decision, risk)
        if route == Route.HUMAN_REVIEW:
            return self._queue_for_review(task, decision, risk)
        return self._execute(
            task,
            decision,
            risk,
            review_id=None,
            success_message="Low-risk request approved automatically and executed (simulated).",
        )

    def approve(
        self,
        review_id: str,
        reviewer: str = DEFAULT_REVIEWER,
        note: str | None = None,
    ) -> TaskResponse:
        item = approve_review(
            review_id,
            reviewer=reviewer,
            note=note,
            queue=self.review_queue,
            audit=self.audit,
        )
        return self._execute(
            item.task,
            item.decision,
            item.risk,
            review_id=item.review_id,
            success_message="Review approved. Action executed (simulated).",
        )

    def reject(
        self,
        review_id: str,
        reviewer: str = DEFAULT_REVIEWER,
        note: str | None = None,
    ) -> TaskResponse:
        item = reject_review(
            review_id,
            reviewer=reviewer,
            note=note,
            queue=self.review_queue,
            audit=self.audit,
        )
        block_action(
            item.task,
            "rejected by human reviewer",
            decision=DecisionType.DENY,
            audit=self.audit,
        )
        return TaskResponse(
            task_id=item.task.task_id,
            status=WorkflowStatus.REJECTED,
            decision=item.decision,
            risk=item.risk,
            review_id=item.review_id,
            message="Review rejected. No action was executed.",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _validate_context(self, task: Task) -> None:
        problems: list[str] = []
        if not task.user_input.strip():
            problems.append("user_input must not be blank")
        if not task.target_system.strip():
            problems.append("target_system must not be blank")
        if not problems:
            return

        error = "; ".join(problems)
        self.audit.record(
            task.task_id,
            AuditEventType.EXCEPTION,
            ACTOR_WORKFLOW,
            details={"stage": "context_validation", "error": error},
        )
        raise TaskValidationError(error)

    def _pipeline_failure(self, task: Task, stage: str, exc: Exception) -> WorkflowError:
        error_text = f"{type(exc).__name__}: {exc}"
        record = self.exceptions.add(task.task_id, stage, error_text, 1)
        self.audit.record(
            task.task_id,
            AuditEventType.EXCEPTION,
            ACTOR_WORKFLOW,
            details={
                "stage": stage,
                "error": error_text,
                "exception_id": record.exception_id,
            },
        )
        return WorkflowError(f"{stage} failed for {task.task_id}: {error_text}")

    def _block(self, task: Task, decision: AIDecision, risk: RiskAssessment) -> TaskResponse:
        reason = (
            f"routing blocked the action (risk {risk.risk_level.value}, "
            f"score {risk.risk_score}; AI decision: {decision.decision.value})"
        )
        block_action(task, reason, decision=DecisionType.DENY, audit=self.audit)
        return TaskResponse(
            task_id=task.task_id,
            status=WorkflowStatus.BLOCKED,
            decision=decision,
            risk=risk,
            message="Action blocked: the request is too risky to execute automatically.",
        )

    def _queue_for_review(
        self, task: Task, decision: AIDecision, risk: RiskAssessment
    ) -> TaskResponse:
        item = self.review_queue.add(task, decision, risk)
        self.audit.record(
            task.task_id,
            AuditEventType.REVIEW_REQUIRED,
            ACTOR_ROUTER,
            decision=DecisionType.REVIEW,
            details={
                "review_id": item.review_id,
                "risk_level": risk.risk_level.value,
                "risk_score": risk.risk_score,
            },
        )
        return TaskResponse(
            task_id=task.task_id,
            status=WorkflowStatus.PENDING_REVIEW,
            decision=decision,
            risk=risk,
            review_id=item.review_id,
            message="Human review required. The action has not been executed.",
        )

    def _execute(
        self,
        task: Task,
        decision: AIDecision,
        risk: RiskAssessment,
        review_id: str | None,
        success_message: str,
    ) -> TaskResponse:
        try:
            result = execute_action(
                task,
                decision=DecisionType.ALLOW,
                audit=self.audit,
                exceptions=self.exceptions,
                max_attempts=self.max_attempts,
                base_delay=self.base_delay,
                sleep=self.sleep,
            )
        except ExecutionFailedError as exc:
            return TaskResponse(
                task_id=task.task_id,
                status=WorkflowStatus.FAILED,
                decision=decision,
                risk=risk,
                review_id=review_id,
                message=(
                    f"Execution failed after {exc.record.attempts} attempt(s). "
                    f"See exception {exc.record.exception_id}."
                ),
            )
        return TaskResponse(
            task_id=task.task_id,
            status=WorkflowStatus.EXECUTED,
            decision=decision,
            risk=risk,
            review_id=review_id,
            execution=result,
            message=success_message,
        )


_default_engine: WorkflowEngine | None = None


def get_workflow_engine() -> WorkflowEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = WorkflowEngine()
    return _default_engine