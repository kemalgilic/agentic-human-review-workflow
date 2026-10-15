import pytest

from app.human_review.decisions import approve_review, reject_review
from app.human_review.queue import (
    ReviewAlreadyDecidedError,
    ReviewNotFoundError,
    ReviewQueue,
    get_review_queue,
)
from app.schemas import (
    AIDecision,
    AuditEventType,
    DecisionType,
    ReviewStatus,
    RiskAssessment,
    Task,
)
from app.services.audit import AuditLog


@pytest.fixture
def queue() -> ReviewQueue:
    return ReviewQueue()


@pytest.fixture
def audit() -> AuditLog:
    return AuditLog()


def add_item(queue: ReviewQueue):
    task = Task(
        user_input="Send the quarterly update to the client",
        requested_action="send_email",
        target_system="mail",
        context={"external_recipient": True},
    )
    decision = AIDecision(
        decision="review",
        confidence=0.7,
        explanation="External action",
        recommended_action="route_to_human_reviewer",
    )
    risk = RiskAssessment(risk_level="medium", risk_score=50, reasons=["external recipient"])
    return queue.add(task, decision, risk)


def test_add_creates_pending_item(queue):
    item = add_item(queue)
    assert item.status == ReviewStatus.PENDING
    assert item.reviewer is None
    assert item.reviewed_at is None
    assert len(queue) == 1


def test_get_unknown_review_raises(queue):
    with pytest.raises(ReviewNotFoundError):
        queue.get("rev_missing")


def test_list_filters_by_status(queue, audit):
    first = add_item(queue)
    second = add_item(queue)
    add_item(queue)
    approve_review(first.review_id, queue=queue, audit=audit)
    reject_review(second.review_id, queue=queue, audit=audit)

    assert len(queue.list_items()) == 3
    assert len(queue.list_items(ReviewStatus.PENDING)) == 1
    assert len(queue.list_items(ReviewStatus.APPROVED)) == 1
    assert len(queue.list_items(ReviewStatus.REJECTED)) == 1


def test_approve_sets_decision_fields(queue, audit):
    item = add_item(queue)
    result = approve_review(
        item.review_id, reviewer="alice", note="looks fine", queue=queue, audit=audit
    )
    assert result.status == ReviewStatus.APPROVED
    assert result.reviewer == "alice"
    assert result.reviewer_note == "looks fine"
    assert result.reviewed_at is not None
    assert result.reviewed_at.tzinfo is not None
    assert queue.get(item.review_id).status == ReviewStatus.APPROVED


def test_reject_sets_decision_fields(queue, audit):
    item = add_item(queue)
    result = reject_review(
        item.review_id, reviewer="bob", note="not allowed", queue=queue, audit=audit
    )
    assert result.status == ReviewStatus.REJECTED
    assert result.reviewer == "bob"
    assert result.reviewer_note == "not allowed"
    assert queue.get(item.review_id).status == ReviewStatus.REJECTED


def test_note_is_optional_and_reviewer_has_default(queue, audit):
    item = add_item(queue)
    result = approve_review(item.review_id, queue=queue, audit=audit)
    assert result.reviewer == "human_reviewer"
    assert result.reviewer_note is None


def test_cannot_approve_twice(queue, audit):
    item = add_item(queue)
    approve_review(item.review_id, queue=queue, audit=audit)
    with pytest.raises(ReviewAlreadyDecidedError):
        approve_review(item.review_id, queue=queue, audit=audit)


def test_cannot_reject_after_approval(queue, audit):
    item = add_item(queue)
    approve_review(item.review_id, queue=queue, audit=audit)
    with pytest.raises(ReviewAlreadyDecidedError):
        reject_review(item.review_id, queue=queue, audit=audit)
    assert queue.get(item.review_id).status == ReviewStatus.APPROVED


def test_decide_unknown_review_raises(queue, audit):
    with pytest.raises(ReviewNotFoundError):
        approve_review("rev_missing", queue=queue, audit=audit)


def test_approval_is_audited(queue, audit):
    item = add_item(queue)
    approve_review(item.review_id, reviewer="alice", note="ok", queue=queue, audit=audit)
    events = audit.list_events(item.task.task_id)
    assert len(events) == 1
    assert events[0].event == AuditEventType.REVIEW_APPROVED
    assert events[0].actor == "alice"
    assert events[0].decision == DecisionType.ALLOW
    assert events[0].details["review_id"] == item.review_id
    assert events[0].details["note"] == "ok"


def test_rejection_is_audited(queue, audit):
    item = add_item(queue)
    reject_review(item.review_id, reviewer="bob", note="no", queue=queue, audit=audit)
    events = audit.list_events(item.task.task_id)
    assert len(events) == 1
    assert events[0].event == AuditEventType.REVIEW_REJECTED
    assert events[0].actor == "bob"
    assert events[0].decision == DecisionType.DENY


def test_failed_decision_writes_no_audit_event(queue, audit):
    item = add_item(queue)
    approve_review(item.review_id, queue=queue, audit=audit)
    with pytest.raises(ReviewAlreadyDecidedError):
        reject_review(item.review_id, queue=queue, audit=audit)
    assert len(audit) == 1


def test_returned_items_are_copies(queue):
    item = add_item(queue)
    item.status = ReviewStatus.APPROVED
    assert queue.get(item.review_id).status == ReviewStatus.PENDING


def test_resolve_rejects_pending_status(queue):
    item = add_item(queue)
    with pytest.raises(ValueError):
        queue.resolve(item.review_id, ReviewStatus.PENDING, "alice", None)


def test_default_queue_is_a_singleton():
    assert get_review_queue() is get_review_queue()