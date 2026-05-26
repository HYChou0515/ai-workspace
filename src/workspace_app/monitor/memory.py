"""In-memory IMonitor — a bounded ring buffer of recent events plus fan-out to
live subscribers. No persistence: a restart wipes it (swap in a durable
backend by subclassing IMonitor if that's needed)."""

from __future__ import annotations

import contextlib
from asyncio import Queue
from collections import deque
from collections.abc import AsyncIterator

from .base import IMonitor, MonitorEvent


class InMemoryMonitor(IMonitor):
    def __init__(self, *, capacity: int = 1000) -> None:
        self._recent: deque[MonitorEvent] = deque(maxlen=capacity)
        self._subs: set[Queue[MonitorEvent]] = set()

    def record(self, event: MonitorEvent) -> None:
        self._recent.append(event)
        for q in self._subs:
            q.put_nowait(event)

    def recent(
        self, *, limit: int | None = None, group_id: str | None = None
    ) -> list[MonitorEvent]:
        items = [e for e in self._recent if group_id is None or e.get("group_id") == group_id]
        return items if limit is None else items[-limit:]

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Queue[MonitorEvent]]:
        q: Queue[MonitorEvent] = Queue()
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)
