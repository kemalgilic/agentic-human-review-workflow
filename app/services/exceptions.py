"""Error types and the exception queue (portfolio demo)."""

from threading import Lock

from app.schemas import ExceptionRecord


class TransientToolError(Exception):
    """A tool call failed in a way that may succeed on retry."""


class PermanentToolError(Exception):
    """A tool call failed in a way that retrying will not fix."""


class ExecutionFailedError(Exception):
    """Raised after a tool action has failed for good."""

    def __init__(self, record: ExceptionRecord) -> None:
        super().__init__(
            f"execution failed for {record.task_id} "
            f"after {record.attempts} attempt(s): {record.error}"
        )
        self.record = record


class ExceptionQueue:
    """Append-only queue of failures that need attention."""

    def __init__(self) -> None:
        self._records: list[ExceptionRecord] = []
        self._lock = Lock()

    def add(self, task_id: str, stage: str, error: str, attempts: int) -> ExceptionRecord:
        record = ExceptionRecord(
            task_id=task_id,
            stage=stage,
            error=error,
            attempts=attempts,
        )
        with self._lock:
            self._records.append(record)
        return record.model_copy(deep=True)

    def list_records(self, task_id: str | None = None) -> list[ExceptionRecord]:
        with self._lock:
            records = list(self._records)
        if task_id is not None:
            records = [r for r in records if r.task_id == task_id]
        return [r.model_copy(deep=True) for r in records]

    def clear(self) -> None:
        """Test helper. Not exposed through the API."""
        with self._lock:
            self._records.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


_default_queue = ExceptionQueue()


def get_exception_queue() -> ExceptionQueue:
    return _default_queue