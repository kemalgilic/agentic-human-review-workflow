"""Simulated tool execution with retry and exception handling.

No real external system is ever called. Every handler returns a fake result.

Failure policy:
- TransientToolError: retried with exponential backoff, up to max_attempts.
- PermanentToolError and any unexpected exception: not retried (an unknown
  failure may not be safe to repeat).
- A failure is never silent: it is written to the exception queue and the
  audit trail, and ExecutionFailedError is raised to the caller.

Demo hooks: a task's context may contain "simulate_transient_failures": N
(the first N attempts fail) or "simulate_permanent_failure": true. They exist
only so the retry and exception paths can be demonstrated and tested.
"""

import time
from collections.abc import Callable
from typing import Any

from app.schemas import (
    AuditEvent,
    AuditEventType,
    DecisionType,
    ExecutionResult,
    RequestedAction,
    Task,
    new_id,
)
from app.services.audit import AuditLog, get_audit_log
from app.services.exceptions import (
    ExceptionQueue,
    ExecutionFailedError,
    PermanentToolError,
    TransientToolError,
    get_exception_queue,
)

ACTOR = "tool_executor"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.2  # seconds; doubles after every failed attempt

ToolHandler = Callable[[Task], dict[str, Any]]


def _send_email(task: Task) -> dict[str, Any]:
    return {"message_id": new_id("msg"), "status": "queued"}


def _update_crm(task: Task) -> dict[str, Any]:
    return {"record_id": new_id("crm"), "status": "updated"}


def _create_ticket(task: Task) -> dict[str, Any]:
    return {"ticket_id": new_id("tkt"), "status": "created"}


def _read_record(task: Task) -> dict[str, Any]:
    return {"record_id": new_id("rec"), "status": "read"}


def _delete_record(task: Task) -> dict[str, Any]:
    return {"record_id": new_id("rec"), "status": "deleted"}


TOOL_REGISTRY: dict[RequestedAction, ToolHandler] = {
    RequestedAction.SEND_EMAIL: _send_email,
    RequestedAction.UPDATE_CRM: _update_crm,
    RequestedAction.CREATE_TICKET: _create_ticket,
    RequestedAction.READ_RECORD: _read_record,
    RequestedAction.DELETE_RECORD: _delete_record,
}


def _maybe_simulate_failure(task: Task, attempt: int) -> None:
    extra = task.context.model_extra or {}

    if extra.get("simulate_permanent_failure") is True:
        raise PermanentToolError("simulated permanent tool failure")

    transient = extra.get("simulate_transient_failures", 0)
    if isinstance(transient, int) and not isinstance(transient, bool) and attempt <= transient:
        raise TransientToolError(f"simulated transient failure on attempt {attempt}")


def _record_failure(
    task: Task,
    exc: Exception,
    attempts: int,
    retryable: bool,
    decision: DecisionType | None,
    audit: AuditLog,
    exceptions: ExceptionQueue,
) -> ExecutionFailedError:
    error_text = f"{type(exc).__name__}: {exc}"
    record = exceptions.add(task.task_id, "execution", error_text, attempts)
    audit.record(
        task_id=task.task_id,
        event=AuditEventType.EXCEPTION,
        actor=ACTOR,
        decision=decision,
        details={
            "stage": "execution",
            "error": error_text,
            "attempts": attempts,
            "retryable": retryable,
            "exception_id": record.exception_id,
        },
    )
    return ExecutionFailedError(record)


def execute_action(
    task: Task,
    *,
    decision: DecisionType | None = None,
    audit: AuditLog | None = None,
    exceptions: ExceptionQueue | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    sleep: Callable[[float], None] = time.sleep,
    registry: dict[RequestedAction, ToolHandler] | None = None,
) -> ExecutionResult:
    """Run the simulated tool action. Raises ExecutionFailedError on failure."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    # Explicit None checks: empty AuditLog / ExceptionQueue / registry are falsy,
    # so "x or default" would silently replace an injected empty instance.
    if audit is None:
        audit = get_audit_log()
    if exceptions is None:
        exceptions = get_exception_queue()
    if registry is None:
        registry = TOOL_REGISTRY

    attempt = 0
    while True:
        attempt += 1
        try:
            _maybe_simulate_failure(task, attempt)
            handler = registry.get(task.requested_action)
            if handler is None:
                raise PermanentToolError(
                    f"no handler registered for '{task.requested_action.value}'"
                )
            output = handler(task)
        except TransientToolError as exc:
            if attempt >= max_attempts:
                raise _record_failure(
                    task, exc, attempt, True, decision, audit, exceptions
                ) from exc
            delay = base_delay * 2 ** (attempt - 1)
            audit.record(
                task_id=task.task_id,
                event=AuditEventType.RETRY_ATTEMPTED,
                actor=ACTOR,
                decision=decision,
                details={
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "error": f"{type(exc).__name__}: {exc}",
                    "next_delay_seconds": delay,
                },
            )
            sleep(delay)
        except PermanentToolError as exc:
            raise _record_failure(
                task, exc, attempt, False, decision, audit, exceptions
            ) from exc
        except Exception as exc:
            raise _record_failure(
                task, exc, attempt, False, decision, audit, exceptions
            ) from exc
        else:
            break

    result = ExecutionResult(
        success=True,
        action=task.requested_action,
        target_system=task.target_system,
        simulated=True,
        message=(
            f"Simulated '{task.requested_action.value}' executed on "
            f"'{task.target_system}' (attempt {attempt})."
        ),
        output=output,
    )
    audit.record(
        task_id=task.task_id,
        event=AuditEventType.ACTION_EXECUTED,
        actor=ACTOR,
        decision=decision,
        details={
            "action": task.requested_action.value,
            "target_system": task.target_system,
            "attempts": attempt,
            "simulated": True,
            "output": output,
        },
    )
    return result


def block_action(
    task: Task,
    reason: str,
    *,
    decision: DecisionType | None = None,
    audit: AuditLog | None = None,
) -> AuditEvent:
    """Record that an action was deliberately not executed."""
    if audit is None:
        audit = get_audit_log()
    return audit.record(
        task_id=task.task_id,
        event=AuditEventType.ACTION_BLOCKED,
        actor=ACTOR,
        decision=decision,
        details={
            "action": task.requested_action.value,
            "target_system": task.target_system,
            "reason": reason,
        },
    )