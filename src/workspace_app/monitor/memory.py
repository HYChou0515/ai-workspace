"""In-memory Monitor — a bounded ring buffer of recent events plus the ABC's
live fan-out. No persistence: a restart wipes it (use SpecstarMonitor for a
durable backend)."""

from __future__ import annotations

from collections import deque

from .base import IMonitor, MonitorEvent


class InMemoryMonitor(IMonitor):
    def __init__(self, *, capacity: int = 1000) -> None:
        super().__init__()
        self._recent: deque[MonitorEvent] = deque(maxlen=capacity)

    def record(self, event: MonitorEvent) -> None:
        self._recent.append(event)
        self._publish(event)

    def recent(
        self,
        *,
        limit: int | None = None,
        group_id: str | None = None,
        kind: str | None = None,
    ) -> list[MonitorEvent]:
        items = [
            e
            for e in self._recent
            if (group_id is None or e.get("group_id") == group_id)
            and (kind is None or e.get("kind") == kind)
        ]
        return items if limit is None else items[-limit:]
