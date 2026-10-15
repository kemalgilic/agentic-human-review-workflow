"""Reviewer decisions: approve or reject a pending review item.

Every successful decision is written to the audit trail. A failed decision
(unknown id, already decided) raises and writes nothing.
"""

from app.human_review.queue import ReviewQueue, get_review_queue
from app.schemas import AuditEventType, DecisionType, ReviewItem, ReviewStatus
from app.services.audit import AuditLog, get_audit_log

DEFAULT_REVIEWER = "human_reviewer"


def _decide(
    review_id: str,
    status: ReviewStatus,
    event: AuditEventType,
    outcome: DecisionType,
    reviewer: str,
    note: str | None,
    queue: ReviewQueue | None,
    audit: AuditLog | None,
) -> ReviewItem:
    # Explicit None checks: ReviewQueue and AuditLog define __len__, so an
    # empty instance is falsy and "queue or default" would silently swap it.
    if queue is None:
        queue = get_review_queue()
    if audit is None:
        audit = get_audit_log()

    item = queue.resolve(review_id, status, reviewer, note)
    audit.record(
        task_id=item.task.task_id,
        event=event,
        actor=reviewer,
        decision=outcome,
        details={
            "review_id": item.review_id,
            "status": item.status.value,
            "note": note,
        },
    )
    return item


def approve_review(
    review_id: str,
    reviewer: str = DEFAULT_REVIEWER,
    note: str | None = None,
    queue: ReviewQueue | None = None,
    audit: AuditLog | None = None,
) -> ReviewItem:
    return _decide(
        review_id,
        ReviewStatus.APPROVED,
        AuditEventType.REVIEW_APPROVED,
        DecisionType.ALLOW,
        reviewer,
        note,
        queue,
        audit,
    )


def reject_review(
    review_id: str,
    reviewer: str = DEFAULT_REVIEWER,
    note: str | None = None,
    queue: ReviewQueue | None = None,
    audit: AuditLog | None = None,
) -> ReviewItem:
    return _decide(
        review_id,
        ReviewStatus.REJECTED,
        AuditEventType.REVIEW_REJECTED,
        DecisionType.DENY,
        reviewer,
        note,
        queue,
        audit,
    )