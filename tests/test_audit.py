from threading import Thread

from app.schemas import AuditEventType, DecisionType
from app.services.audit import AuditLog, get_audit_log


def test_record_creates_complete_event():
    log = AuditLog()
    event = log.record("task_1", AuditEventType.TASK_RECEIVED, "system", details={"k": "v"})
    assert event.task_id == "task_1"
    assert event.event == AuditEventType.TASK_RECEIVED
    assert event.actor == "system"
    assert event.timestamp.tzinfo is not None
    assert event.details == {"k": "v"}
    assert len(log) == 1


def test_events_keep_insertion_order():
    log = AuditLog()
    log.record("task_1", AuditEventType.TASK_RECEIVED, "system")
    log.record("task_1", AuditEventType.DECISION_CREATED, "ai_decision")
    log.record("task_1", AuditEventType.RISK_ASSESSED, "risk_engine")
    kinds = [e.event for e in log.list_events()]
    assert kinds == [
        AuditEventType.TASK_RECEIVED,
        AuditEventType.DECISION_CREATED,
        AuditEventType.RISK_ASSESSED,
    ]


def test_filter_by_task_id():
    log = AuditLog()
    log.record("task_1", AuditEventType.TASK_RECEIVED, "system")
    log.record("task_2", AuditEventType.TASK_RECEIVED, "system")
    log.record("task_1", AuditEventType.ACTION_EXECUTED, "executor")
    assert len(log.list_events("task_1")) == 2
    assert len(log.list_events("task_2")) == 1
    assert log.list_events("unknown") == []


def test_returned_events_are_copies():
    log = AuditLog()
    log.record("task_1", AuditEventType.TASK_RECEIVED, "system")
    events = log.list_events()
    events[0].actor = "tampered"
    events.clear()
    stored = log.list_events()
    assert len(stored) == 1
    assert stored[0].actor == "system"


def test_details_are_isolated_from_caller():
    log = AuditLog()
    details = {"nested": {"count": 1}}
    log.record("task_1", AuditEventType.TASK_RECEIVED, "system", details=details)
    details["nested"]["count"] = 99
    assert log.list_events()[0].details["nested"]["count"] == 1


def test_decision_is_optional_and_stored():
    log = AuditLog()
    log.record("task_1", AuditEventType.TASK_RECEIVED, "system")
    log.record("task_1", AuditEventType.DECISION_CREATED, "ai_decision", decision=DecisionType.REVIEW)
    first, second = log.list_events()
    assert first.decision is None
    assert second.decision == DecisionType.REVIEW


def test_clear_empties_log():
    log = AuditLog()
    log.record("task_1", AuditEventType.TASK_RECEIVED, "system")
    log.clear()
    assert len(log) == 0
    assert log.list_events() == []


def test_concurrent_recording_loses_no_events():
    log = AuditLog()

    def worker(n: int) -> None:
        for _ in range(50):
            log.record(f"task_{n}", AuditEventType.TASK_RECEIVED, "system")

    threads = [Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(log) == 400


def test_default_log_is_a_singleton():
    assert get_audit_log() is get_audit_log()