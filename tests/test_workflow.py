import pytest

from app.human_review.queue import (
    ReviewAlreadyDecidedError,
    ReviewNotFoundError,
    ReviewQueue,
)
from app.schemas import (
    AIDecision,
    AuditEventType,
    DecisionType,
    ReviewStatus,
    RiskLevel,
    TaskCreate,
    WorkflowStatus,
)
from app.services.audit import AuditLog
from app.services.exceptions import ExceptionQueue
from app.services.workflow import (
    TaskValidationError,
    WorkflowEngine,
    WorkflowError,
    get_workflow_engine,
)

E = AuditEventType


class SleepRecorder:
    """Stands in for time.sleep so tests run instantly."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class AlwaysAllow:
    def decide(self, task, risk):
        return AIDecision(
            decision=DecisionType.ALLOW,
            confidence=1.0,
            explanation="stub says allow",
            recommended_action="execute_automatically",
        )


class CrashingProvider:
    def decide(self, task, risk):
        raise RuntimeError("provider down")


@pytest.fixture
def audit() -> AuditLog:
    return AuditLog()


@pytest.fixture
def review_queue() -> ReviewQueue:
    return ReviewQueue()


@pytest.fixture
def exceptions() -> ExceptionQueue:
    return ExceptionQueue()


@pytest.fixture
def sleeper() -> SleepRecorder:
    return SleepRecorder()


def build_engine(audit, review_queue, exceptions, sleeper, provider=None) -> WorkflowEngine:
    return WorkflowEngine(
        audit=audit,
        review_queue=review_queue,
        exceptions=exceptions,
        provider=provider,
        base_delay=0.1,
        sleep=sleeper,
    )


@pytest.fixture
def engine(audit, review_queue, exceptions, sleeper) -> WorkflowEngine:
    return build_engine(audit, review_queue, exceptions, sleeper)


def make_request(
    action: str = "read_record",
    user_input: str = "Please handle this demo request",
    **context,
) -> TaskCreate:
    return TaskCreate(
        user_input=user_input,
        requested_action=action,
        target_system="demo_system",
        context=context,
    )


def kinds(audit: AuditLog, task_id: str | None = None) -> list[AuditEventType]:
    return [e.event for e in audit.list_events(task_id)]


def submit_medium(engine: WorkflowEngine, **extra):
    return engine.submit(make_request("send_email", external_recipient=True, **extra))


# --------------------------------------------------------------------------
# The three routes
# --------------------------------------------------------------------------


def test_low_risk_allow_flow(engine, audit, review_queue):
    response = engine.submit(make_request("read_record"))

    assert response.status == WorkflowStatus.EXECUTED
    assert response.decision.decision == DecisionType.ALLOW
    assert response.risk.risk_level == RiskLevel.LOW
    assert response.review_id is None
    assert response.execution is not None
    assert response.execution.simulated is True
    assert len(review_queue) == 0
    assert kinds(audit, response.task_id) == [
        E.TASK_RECEIVED,
        E.RISK_ASSESSED,
        E.DECISION_CREATED,
        E.ACTION_EXECUTED,
    ]
    decision_event = audit.list_events(response.task_id)[2]
    assert decision_event.actor == "ai_decision"
    assert decision_event.decision == DecisionType.ALLOW


def test_medium_risk_review_flow(engine, audit, review_queue):
    response = submit_medium(engine)

    assert response.status == WorkflowStatus.PENDING_REVIEW
    assert response.decision.decision == DecisionType.REVIEW
    assert response.risk.risk_level == RiskLevel.MEDIUM
    assert response.execution is None
    assert response.review_id is not None
    assert review_queue.get(response.review_id).status == ReviewStatus.PENDING
    assert kinds(audit, response.task_id) == [
        E.TASK_RECEIVED,
        E.RISK_ASSESSED,
        E.DECISION_CREATED,
        E.REVIEW_REQUIRED,
    ]
    assert E.ACTION_EXECUTED not in kinds(audit)


def test_high_risk_deny_flow(engine, audit, review_queue):
    response = engine.submit(make_request("delete_record"))

    assert response.status == WorkflowStatus.BLOCKED
    assert response.decision.decision == DecisionType.DENY
    assert response.risk.risk_level == RiskLevel.HIGH
    assert response.execution is None
    assert response.review_id is None
    assert len(review_queue) == 0
    assert kinds(audit, response.task_id) == [
        E.TASK_RECEIVED,
        E.RISK_ASSESSED,
        E.DECISION_CREATED,
        E.ACTION_BLOCKED,
    ]
    assert E.ACTION_EXECUTED not in kinds(audit)


# --------------------------------------------------------------------------
# Human review: approve and reject
# --------------------------------------------------------------------------


def test_approval_executes_action(engine, audit, review_queue):
    pending = submit_medium(engine)
    response = engine.approve(pending.review_id, reviewer="alice", note="ok")

    assert response.status == WorkflowStatus.EXECUTED
    assert response.task_id == pending.task_id
    assert response.review_id == pending.review_id
    assert response.execution is not None
    assert review_queue.get(pending.review_id).status == ReviewStatus.APPROVED
    assert kinds(audit, pending.task_id) == [
        E.TASK_RECEIVED,
        E.RISK_ASSESSED,
        E.DECISION_CREATED,
        E.REVIEW_REQUIRED,
        E.REVIEW_APPROVED,
        E.ACTION_EXECUTED,
    ]
    events = audit.list_events(pending.task_id)
    assert events[4].actor == "alice"
    assert events[5].decision == DecisionType.ALLOW


def test_rejection_blocks_action(engine, audit, review_queue):
    pending = submit_medium(engine)
    response = engine.reject(pending.review_id, reviewer="bob", note="not allowed")

    assert response.status == WorkflowStatus.REJECTED
    assert response.execution is None
    item = review_queue.get(pending.review_id)
    assert item.status == ReviewStatus.REJECTED
    assert item.reviewer_note == "not allowed"
    assert kinds(audit, pending.task_id) == [
        E.TASK_RECEIVED,
        E.RISK_ASSESSED,
        E.DECISION_CREATED,
        E.REVIEW_REQUIRED,
        E.REVIEW_REJECTED,
        E.ACTION_BLOCKED,
    ]
    assert E.ACTION_EXECUTED not in kinds(audit)


def test_cannot_approve_twice(engine, audit):
    pending = submit_medium(engine)
    engine.approve(pending.review_id)
    with pytest.raises(ReviewAlreadyDecidedError):
        engine.approve(pending.review_id)
    assert kinds(audit, pending.task_id).count(E.ACTION_EXECUTED) == 1


def test_cannot_approve_after_rejection(engine, audit):
    pending = submit_medium(engine)
    engine.reject(pending.review_id)
    with pytest.raises(ReviewAlreadyDecidedError):
        engine.approve(pending.review_id)
    assert E.ACTION_EXECUTED not in kinds(audit)


@pytest.mark.parametrize("method", ["approve", "reject"])
def test_unknown_review_raises(engine, method):
    with pytest.raises(ReviewNotFoundError):
        getattr(engine, method)("rev_missing")


# --------------------------------------------------------------------------
# Fail-safe routing: the AI can never loosen the risk engine
# --------------------------------------------------------------------------


def test_ai_allow_cannot_override_high_risk(audit, review_queue, exceptions, sleeper):
    engine = build_engine(audit, review_queue, exceptions, sleeper, provider=AlwaysAllow())
    response = engine.submit(make_request("delete_record"))

    assert response.status == WorkflowStatus.BLOCKED
    blocked = audit.list_events(response.task_id)[-1]
    assert blocked.event == E.ACTION_BLOCKED
    assert blocked.decision == DecisionType.DENY
    assert "AI decision: allow" in blocked.details["reason"]
    assert E.ACTION_EXECUTED not in kinds(audit)


def test_ai_allow_cannot_skip_review_for_medium_risk(audit, review_queue, exceptions, sleeper):
    engine = build_engine(audit, review_queue, exceptions, sleeper, provider=AlwaysAllow())
    response = submit_medium(engine)

    assert response.status == WorkflowStatus.PENDING_REVIEW
    assert len(review_queue) == 1
    assert E.ACTION_EXECUTED not in kinds(audit, response.task_id)


# --------------------------------------------------------------------------
# Retry and exception handling
# --------------------------------------------------------------------------


def test_transient_failure_is_retried_in_auto_flow(engine, audit, exceptions, sleeper):
    response = engine.submit(make_request("read_record", simulate_transient_failures=1))

    assert response.status == WorkflowStatus.EXECUTED
    assert kinds(audit, response.task_id) == [
        E.TASK_RECEIVED,
        E.RISK_ASSESSED,
        E.DECISION_CREATED,
        E.RETRY_ATTEMPTED,
        E.ACTION_EXECUTED,
    ]
    assert sleeper.delays == [0.1]
    assert len(exceptions) == 0


def test_permanent_failure_in_auto_flow_is_not_silent(engine, audit, exceptions):
    response = engine.submit(make_request("read_record", simulate_permanent_failure=True))

    assert response.status == WorkflowStatus.FAILED
    assert response.execution is None
    assert kinds(audit, response.task_id) == [
        E.TASK_RECEIVED,
        E.RISK_ASSESSED,
        E.DECISION_CREATED,
        E.EXCEPTION,
    ]
    records = exceptions.list_records()
    assert len(records) == 1
    assert records[0].task_id == response.task_id
    assert records[0].exception_id in response.message


def test_retries_exhausted_in_auto_flow(engine, audit, exceptions, sleeper):
    response = engine.submit(make_request("read_record", simulate_transient_failures=9))

    assert response.status == WorkflowStatus.FAILED
    assert kinds(audit, response.task_id).count(E.RETRY_ATTEMPTED) == 2
    assert kinds(audit, response.task_id)[-1] == E.EXCEPTION
    assert E.ACTION_EXECUTED not in kinds(audit)
    assert exceptions.list_records()[0].attempts == 3
    assert sleeper.delays == [0.1, 0.2]


def test_failure_after_approval_is_not_silent(engine, audit, review_queue, exceptions):
    pending = submit_medium(engine, simulate_permanent_failure=True)
    response = engine.approve(pending.review_id)

    assert response.status == WorkflowStatus.FAILED
    assert response.execution is None
    assert review_queue.get(pending.review_id).status == ReviewStatus.APPROVED
    assert kinds(audit, pending.task_id) == [
        E.TASK_RECEIVED,
        E.RISK_ASSESSED,
        E.DECISION_CREATED,
        E.REVIEW_REQUIRED,
        E.REVIEW_APPROVED,
        E.EXCEPTION,
    ]
    assert len(exceptions) == 1


# --------------------------------------------------------------------------
# Validation and pipeline failures
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["user_input", "target_system"])
def test_blank_fields_fail_validation(engine, audit, review_queue, field):
    data = {
        "user_input": "Please handle this demo request",
        "requested_action": "read_record",
        "target_system": "demo_system",
    }
    data[field] = "   "

    with pytest.raises(TaskValidationError):
        engine.submit(TaskCreate(**data))

    events = audit.list_events()
    assert [e.event for e in events] == [E.TASK_RECEIVED, E.EXCEPTION]
    assert events[1].details["stage"] == "context_validation"
    assert len(review_queue) == 0


def test_decision_provider_crash_is_recorded(audit, review_queue, exceptions, sleeper):
    engine = build_engine(
        audit, review_queue, exceptions, sleeper, provider=CrashingProvider()
    )

    with pytest.raises(WorkflowError) as exc_info:
        engine.submit(make_request("read_record"))

    assert "provider down" in str(exc_info.value)
    records = exceptions.list_records()
    assert len(records) == 1
    assert records[0].stage == "risk_and_decision"
    assert kinds(audit) == [E.TASK_RECEIVED, E.RISK_ASSESSED, E.EXCEPTION]


# --------------------------------------------------------------------------
# Audit trail properties
# --------------------------------------------------------------------------


def test_trails_are_independent_per_task(engine, audit):
    executed = engine.submit(make_request("read_record"))
    blocked = engine.submit(make_request("delete_record"))

    assert E.ACTION_EXECUTED in kinds(audit, executed.task_id)
    assert E.ACTION_BLOCKED not in kinds(audit, executed.task_id)
    assert E.ACTION_BLOCKED in kinds(audit, blocked.task_id)
    assert E.ACTION_EXECUTED not in kinds(audit, blocked.task_id)


def test_audit_trail_is_chronological(engine, audit):
    pending = submit_medium(engine)
    engine.approve(pending.review_id)
    timestamps = [e.timestamp for e in audit.list_events()]
    assert timestamps == sorted(timestamps)


def test_default_engine_is_a_singleton():
    assert get_workflow_engine() is get_workflow_engine()