"""Append-only in-memory audit trail (portfolio demo).

Events are never modified after they are recorded. Callers only ever receive
copies, so the stored trail cannot be altered from the outside.
"""

from copy import deepcopy
from threading import Lock
from typing import Any

from app.schemas import AuditEvent, AuditEventType, DecisionType


class AuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = Lock()

    def record(
        self,
        task_id: str,
        event: AuditEventType,
        actor: str,
        decision: DecisionType | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        entry = AuditEvent(
            task_id=task_id,
            event=event,
            actor=actor,
            decision=decision,
            details=deepcopy(details) if details else {},
        )
        with self._lock:
            self._events.append(entry)
        return entry.model_copy(deep=True)

    def list_events(self, task_id: str | None = None) -> list[AuditEvent]:
        with self._lock:
            events = list(self._events)
        if task_id is not None:
            events = [e for e in events if e.task_id == task_id]
        return [e.model_copy(deep=True) for e in events]

    def clear(self) -> None:
        """Test helper. Not exposed through the API."""
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


_default_log = AuditLog()


def get_audit_log() -> AuditLog:
    return _default_log