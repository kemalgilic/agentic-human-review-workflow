"""In-memory human review queue (portfolio demo)."""

from threading import Lock

from app.schemas import AIDecision, ReviewItem, ReviewStatus, RiskAssessment, Task, utc_now


class ReviewNotFoundError(Exception):
    pass


class ReviewAlreadyDecidedError(Exception):
    pass


class ReviewQueue:
    def __init__(self) -> None:
        self._items: dict[str, ReviewItem] = {}
        self._lock = Lock()

    def add(self, task: Task, decision: AIDecision, risk: RiskAssessment) -> ReviewItem:
        item = ReviewItem(task=task, decision=decision, risk=risk)
        with self._lock:
            self._items[item.review_id] = item
        return item.model_copy(deep=True)

    def get(self, review_id: str) -> ReviewItem:
        with self._lock:
            item = self._items.get(review_id)
            if item is None:
                raise ReviewNotFoundError(review_id)
            return item.model_copy(deep=True)

    def list_items(self, status: ReviewStatus | None = None) -> list[ReviewItem]:
        with self._lock:
            items = list(self._items.values())
        if status is not None:
            items = [i for i in items if i.status == status]
        return [i.model_copy(deep=True) for i in items]

    def resolve(
        self,
        review_id: str,
        status: ReviewStatus,
        reviewer: str,
        note: str | None,
    ) -> ReviewItem:
        """Atomically move a pending item to approved or rejected."""
        if status == ReviewStatus.PENDING:
            raise ValueError("resolve() requires a final status")
        with self._lock:
            item = self._items.get(review_id)
            if item is None:
                raise ReviewNotFoundError(review_id)
            if item.status != ReviewStatus.PENDING:
                raise ReviewAlreadyDecidedError(
                    f"review {review_id} is already {item.status.value}"
                )
            item.status = status
            item.reviewer = reviewer
            item.reviewer_note = note
            item.reviewed_at = utc_now()
            return item.model_copy(deep=True)

    def clear(self) -> None:
        """Test helper. Not exposed through the API."""
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


_default_queue = ReviewQueue()


def get_review_queue() -> ReviewQueue:
    return _default_queue