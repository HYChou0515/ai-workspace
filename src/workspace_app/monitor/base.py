"""IMonitor interface (issue #11) — the live telemetry sink.

`IMonitor` is the abstract contract; concrete backends subclass it (in-memory
today, a persistent/external one later) without callers changing. `sse` is a
concrete template built on the abstract `subscribe`, so impls only provide
`record` / `recent` / `subscribe`.
"""

from __future__ import annotations

import abc
import json
from asyncio import Queue
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

MonitorEvent = dict[str, Any]


class IMonitor(abc.ABC):
    @abc.abstractmethod
    def record(self, event: MonitorEvent) -> None:
        """Append a telemetry event and fan it out to live subscribers."""
        ...

    @abc.abstractmethod
    def recent(
        self, *, limit: int | None = None, group_id: str | None = None
    ) -> list[MonitorEvent]:
        """Recent events oldest→newest, optionally scoped to `group_id` (the
        investigation a trace was tagged with) and capped to the last `limit`."""
        ...

    @abc.abstractmethod
    def subscribe(self) -> AbstractAsyncContextManager[Queue[MonitorEvent]]:
        """Async context manager yielding a queue every recorded event is pushed
        onto, until the consumer leaves the context."""
        ...

    async def sse(self, *, group_id: str | None = None) -> AsyncIterator[str]:
        """Server-sent-events of live telemetry — `data: <json>\\n\\n` per event,
        filtered to `group_id` when given. Backs the /monitor/stream endpoint."""
        async with self.subscribe() as q:
            while True:
                event = await q.get()
                if group_id is None or event.get("group_id") == group_id:
                    yield f"data: {json.dumps(event)}\n\n"
