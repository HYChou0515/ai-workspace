"""Specstar-backed Monitor — each telemetry event is a first-class specstar
resource, so events survive restarts and ride specstar's swappable storage
backend (and get its CRUD routes for free). The live feed is still the ABC's
in-memory fan-out, since specstar doesn't push.

`record` writes synchronously — fine because spans are low-frequency (a few per
turn), not per-token. `recent` pushes the `group_id` filter **and** the sort +
limit into the query (both `group_id` and `seq` are indexed), so it only loads
the rows it returns rather than the whole history. Append-only, so consider
pruning for very long runs.
"""

from __future__ import annotations

import contextlib
import operator
from functools import reduce
from typing import Any

from msgspec import Struct, field
from specstar import QB, SpecStar

from .base import IMonitor, MonitorEvent


class TelemetryEvent(Struct):  # → resource "telemetry-event"
    kind: str
    seq: int  # monotonic per-monitor order key (created_time can collide)
    group_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class SpecstarMonitor(IMonitor):
    def __init__(self, spec: SpecStar) -> None:
        super().__init__()
        # add_model is one-shot per spec; tolerate a second monitor over the
        # same spec (the events persist in the spec's storage either way).
        # group_id + seq are indexed so recent() can filter/sort/limit in-query.
        with contextlib.suppress(ValueError):
            spec.add_model(TelemetryEvent, indexed_fields=["group_id", "seq", "kind"])
        self._rm = spec.get_resource_manager(TelemetryEvent)
        # Resume the order key past whatever's already persisted (append-only).
        self._seq = self._rm.count_resources(QB.all().build())

    def record(self, event: MonitorEvent) -> None:
        self._seq += 1
        self._rm.create(
            TelemetryEvent(
                kind=str(event.get("kind", "")),
                seq=self._seq,
                group_id=event.get("group_id"),
                payload=event,
            )
        )
        self._publish(event)

    def recent(
        self,
        *,
        limit: int | None = None,
        group_id: str | None = None,
        kind: str | None = None,
    ) -> list[MonitorEvent]:
        conds = []
        if group_id is not None:
            conds.append(QB["group_id"] == group_id)
        if kind is not None:
            conds.append(QB["kind"] == kind)
        base = reduce(operator.and_, conds) if conds else QB.all()
        if limit is None:
            query = base.sort("seq").build()  # oldest→newest, all
        else:
            query = base.sort("-seq").limit(limit).build()  # newest `limit`, desc
        events = [self._payload(r) for r in self._rm.list_resources(query)]
        return events if limit is None else events[::-1]  # flip desc back to oldest→newest

    @staticmethod
    def _payload(r: Any) -> MonitorEvent:
        data = r.data
        assert isinstance(data, TelemetryEvent)  # narrow for ty (coverage-clean)
        return data.payload
