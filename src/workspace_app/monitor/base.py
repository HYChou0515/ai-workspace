"""Monitor interface (issue #11) — the live telemetry sink.

`IMonitor` is the abstract contract; concrete backends subclass it (in-memory,
specstar-persistent, …) without callers changing. The live fan-out
(`subscribe` / `sse` / `_publish`) is shared template logic on the ABC, so a
backend only implements `record` (persist) and `recent` (read back).
"""

from __future__ import annotations

import abc
import contextlib
import json
from asyncio import Queue
from collections.abc import AsyncIterator
from typing import Any

MonitorEvent = dict[str, Any]


class IMonitor(abc.ABC):
    def __init__(self) -> None:
        # Live subscribers' queues — specstar/persistence doesn't push, so the
        # real-time feed is always served from this in-memory fan-out.
        self._subs: set[Queue[MonitorEvent]] = set()

    @abc.abstractmethod
    def record(self, event: MonitorEvent) -> None:
        """Store a telemetry event. Impls should also `self._publish(event)` so
        live subscribers see it."""
        ...

    @abc.abstractmethod
    def recent(
        self,
        *,
        limit: int | None = None,
        group_id: str | None = None,
        kind: str | None = None,
    ) -> list[MonitorEvent]:
        """Recent events oldest→newest, optionally scoped to `group_id` (the
        investigation a trace was tagged with) and/or `kind` (the event type,
        e.g. #407's ``mirror`` / ``restore`` / ``ws_census``), capped to the
        last `limit`."""
        ...

    def _publish(self, event: MonitorEvent) -> None:
        for q in self._subs:
            q.put_nowait(event)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Queue[MonitorEvent]]:
        """Live feed: yields a queue every recorded event is pushed onto, until
        the consumer leaves the context."""
        q: Queue[MonitorEvent] = Queue()
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)

    async def sse(self, *, group_id: str | None = None) -> AsyncIterator[str]:
        """Server-sent-events of live telemetry — `data: <json>\\n\\n` per event,
        filtered to `group_id` when given. Backs the /monitor/stream endpoint."""
        async with self.subscribe() as q:
            while True:
                event = await q.get()
                if group_id is None or event.get("group_id") == group_id:
                    yield f"data: {json.dumps(event)}\n\n"
