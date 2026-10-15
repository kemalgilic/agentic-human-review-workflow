import pytest

from app.schemas import AuditEventType, DecisionType, RequestedAction, Task
from app.services.audit import AuditLog, get_audit_log
from app.services.exceptions import (
    ExceptionQueue,
    ExecutionFailedError,
    get_exception_queue,
)
from app.services.execution import block_action, execute_action


def make_task(action: str = "read_record", **context) -> Task:
    return Task(
        user_input="Run the demo action for this task",
        requested_action=action,
        target_system="demo_system",
        context=context,
    )


class SleepRecorder:
    """Stands in for time.sleep so tests run instantly."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture
def audit() -> AuditLog:
    return AuditLog()


@pytest.fixture
def exceptions() -> ExceptionQueue:
    return ExceptionQueue()


@pytest.fixture
def sleeper() -> SleepRecorder:
    return SleepRecorder()


def run(task, audit, exceptions, sleeper, **kwargs):
    kwargs.setdefault("base_delay", 0.1)
    return execute_action(
        task, audit=audit, exceptions=exceptions, sleep=sleeper, **kwargs
    )


def event_kinds(audit: AuditLog) -> list[AuditEventType]:
    return [e.event for e in audit.list_events()]


@pytest.mark.parametrize("action", list(RequestedAction))
def test_every_action_has_a_simulated_handler(action, audit, exceptions, sleeper):
    result = run(make_task(action.value), audit, exceptions, sleeper)
    assert result.success is True
    assert result.simulated is True
    assert result.action == action
    assert result.output


def test_success_is_audited(audit, exceptions, sleeper):
    task = make_task("send_email")
    run(task, audit, exceptions, sleeper, decision=DecisionType.ALLOW)
    events = audit.list_events(task.task_id)
    assert [e.event for e in events] == [AuditEventType.ACTION_EXECUTED]
    assert events[0].actor == "tool_executor"
    assert events[0].decision == DecisionType.ALLOW
    assert events[0].details["attempts"] == 1
    assert events[0].details["simulated"] is True
    assert sleeper.delays == []


def test_success_records_no_exception(audit, exceptions, sleeper):
    run(make_task("read_record"), audit, exceptions, sleeper)
    assert len(exceptions) == 0


def test_transient_failure_is_retried_then_succeeds(audit, exceptions, sleeper):
    task = make_task("update_crm", simulate_transient_failures=2)
    result = run(task, audit, exceptions, sleeper)
    assert result.success is True
    assert event_kinds(audit) == [
        AuditEventType.RETRY_ATTEMPTED,
        AuditEventType.RETRY_ATTEMPTED,
        AuditEventType.ACTION_EXECUTED,
    ]
    assert audit.list_events()[-1].details["attempts"] == 3
    assert sleeper.delays == [0.1, 0.2]
    assert len(exceptions) == 0


def test_retries_exhausted_raises_and_queues(audit, exceptions, sleeper):
    task = make_task("create_ticket", simulate_transient_failures=5)
    with pytest.raises(ExecutionFailedError) as exc_info:
        run(task, audit, exceptions, sleeper, max_attempts=3)

    record = exc_info.value.record
    assert record.task_id == task.task_id
    assert record.stage == "execution"
    assert record.attempts == 3
    assert "TransientToolError" in record.error
    assert len(exceptions) == 1
    assert event_kinds(audit) == [
        AuditEventType.RETRY_ATTEMPTED,
        AuditEventType.RETRY_ATTEMPTED,
        AuditEventType.EXCEPTION,
    ]
    assert AuditEventType.ACTION_EXECUTED not in event_kinds(audit)
    assert sleeper.delays == [0.1, 0.2]


def test_failure_audit_event_has_details(audit, exceptions, sleeper):
    task = make_task("create_ticket", simulate_transient_failures=9)
    with pytest.raises(ExecutionFailedError) as exc_info:
        run(task, audit, exceptions, sleeper, max_attempts=2)

    details = audit.list_events()[-1].details
    assert details["exception_id"] == exc_info.value.record.exception_id
    assert details["retryable"] is True
    assert details["attempts"] == 2
    assert details["stage"] == "execution"


def test_permanent_failure_is_not_retried(audit, exceptions, sleeper):
    task = make_task("send_email", simulate_permanent_failure=True)
    with pytest.raises(ExecutionFailedError) as exc_info:
        run(task, audit, exceptions, sleeper)

    assert exc_info.value.record.attempts == 1
    assert event_kinds(audit) == [AuditEventType.EXCEPTION]
    assert audit.list_events()[0].details["retryable"] is False
    assert sleeper.delays == []
    assert len(exceptions) == 1


def test_unexpected_error_is_not_swallowed(audit, exceptions, sleeper):
    def boom(task):
        raise RuntimeError("boom")

    registry = {RequestedAction.READ_RECORD: boom}
    with pytest.raises(ExecutionFailedError) as exc_info:
        run(make_task("read_record"), audit, exceptions, sleeper, registry=registry)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "boom" in exc_info.value.record.error
    assert exc_info.value.record.attempts == 1
    assert sleeper.delays == []
    assert len(exceptions) == 1


def test_missing_handler_fails_permanently(audit, exceptions, sleeper):
    with pytest.raises(ExecutionFailedError) as exc_info:
        run(make_task("read_record"), audit, exceptions, sleeper, registry={})

    assert exc_info.value.record.attempts == 1
    assert "no handler" in exc_info.value.record.error
    assert event_kinds(audit) == [AuditEventType.EXCEPTION]


def test_single_attempt_policy_does_not_retry(audit, exceptions, sleeper):
    task = make_task("update_crm", simulate_transient_failures=1)
    with pytest.raises(ExecutionFailedError) as exc_info:
        run(task, audit, exceptions, sleeper, max_attempts=1)

    assert exc_info.value.record.attempts == 1
    assert event_kinds(audit) == [AuditEventType.EXCEPTION]
    assert sleeper.delays == []


def test_invalid_max_attempts_is_rejected(audit, exceptions, sleeper):
    with pytest.raises(ValueError):
        run(make_task(), audit, exceptions, sleeper, max_attempts=0)
    assert len(audit) == 0
    assert len(exceptions) == 0


def test_block_action_is_audited(audit):
    task = make_task("delete_record")
    event = block_action(task, "high risk request", decision=DecisionType.DENY, audit=audit)

    assert event.event == AuditEventType.ACTION_BLOCKED
    assert event.decision == DecisionType.DENY
    assert event.details["reason"] == "high risk request"
    assert event.details["action"] == "delete_record"
    assert event_kinds(audit) == [AuditEventType.ACTION_BLOCKED]


def test_exception_queue_filters_and_returns_copies():
    queue = ExceptionQueue()
    queue.add("task_1", "execution", "err one", 2)
    queue.add("task_2", "execution", "err two", 1)

    assert len(queue.list_records()) == 2
    only = queue.list_records("task_1")
    assert len(only) == 1
    assert only[0].error == "err one"

    only[0].error = "tampered"
    assert queue.list_records("task_1")[0].error == "err one"

    queue.clear()
    assert len(queue) == 0


def test_injected_empty_queues_are_respected(sleeper):
    global_audit_before = len(get_audit_log())
    global_exceptions_before = len(get_exception_queue())

    audit = AuditLog()
    exceptions = ExceptionQueue()
    task = make_task("send_email", simulate_permanent_failure=True)
    with pytest.raises(ExecutionFailedError):
        execute_action(task, audit=audit, exceptions=exceptions, sleep=sleeper)

    assert len(exceptions) == 1
    assert len(audit) == 1
    assert len(get_audit_log()) == global_audit_before
    assert len(get_exception_queue()) == global_exceptions_before


def test_default_exception_queue_is_a_singleton():
    assert get_exception_queue() is get_exception_queue()