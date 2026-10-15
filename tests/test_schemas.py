import pytest
from pydantic import ValidationError

from app.schemas import (
    AIDecision,
    AuditEvent,
    AuditEventType,
    ReviewItem,
    ReviewStatus,
    RiskAssessment,
    Task,
    TaskCreate,
)


def test_task_gets_id_and_timestamp():
    task = Task(
        user_input="Send the weekly report",
        requested_action="send_email",
        target_system="mail",
    )
    assert task.task_id.startswith("task_")
    assert task.created_at.tzinfo is not None
    assert task.context.external_recipient is False


def test_task_rejects_unknown_action():
    with pytest.raises(ValidationError):
        Task(user_input="x", requested_action="launch_rocket", target_system="mail")


def test_task_rejects_empty_input():
    with pytest.raises(ValidationError):
        TaskCreate(user_input="", requested_action="read_record", target_system="crm")


def test_ai_decision_confidence_bounds():
    with pytest.raises(ValidationError):
        AIDecision(
            decision="allow",
            confidence=1.5,
            explanation="x",
            recommended_action="x",
        )


def test_risk_score_bounds():
    with pytest.raises(ValidationError):
        RiskAssessment(risk_level="low", risk_score=101, reasons=[])


def test_review_item_defaults_to_pending():
    task = Task(
        user_input="Update contact",
        requested_action="update_crm",
        target_system="crm",
    )
    item = ReviewItem(
        task=task,
        decision=AIDecision(
            decision="review",
            confidence=0.6,
            explanation="External action",
            recommended_action="human_review",
        ),
        risk=RiskAssessment(risk_level="medium", risk_score=50, reasons=["external action"]),
    )
    assert item.status == ReviewStatus.PENDING
    assert item.review_id.startswith("rev_")
    assert item.reviewed_at is None


def test_audit_event_defaults():
    event = AuditEvent(task_id="task_1", event=AuditEventType.TASK_RECEIVED, actor="system")
    assert event.timestamp.tzinfo is not None
    assert event.details == {}
    assert event.decision is None