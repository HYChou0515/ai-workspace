"""Specstar-backed Monitor — each telemetry event is a first-class specstar
resource, so events survive restarts and ride specstar's swappable storage
backend (and get its CRUD routes for free). The live feed is still the ABC's
in-memory fan-out, since specstar doesn't push.

`record` writes synchronously — fine because spans are low-frequency (a few per
turn), not per-token. `recent` pushes the `group_id` filter **and** the sort +
limit into the query (both `group_id` and `seq` are indexed), so it only loads
the rows it returns rather than the whole history. Rows older than
``observability.telemetry.keep_days`` are removed by
:func:`purge_telemetry_older_than`, run as a job on the ``blob-gc`` queue
(docs/plan-telemetry-retention.md): a generation span carries the whole
prompt, so an unpurged table grew to hundreds of GB.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import operator
import time
from collections.abc import Callable
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


PURGE_BATCH = 500
PURGE_BUDGET_S = 600.0


def purge_telemetry_older_than(
    spec: SpecStar,
    cutoff: dt.datetime,
    *,
    batch: int = PURGE_BATCH,
    budget_s: float = PURGE_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Permanently delete telemetry-event rows created before ``cutoff``,
    oldest first, ``batch`` at a time, until none are left or ``budget_s`` is
    spent; return how many went (docs/plan-telemetry-retention.md D6/D7).

    ``permanently_delete``, not ``delete``: a soft delete keeps the row and its
    revisions, so no space would come back. Bounded by the budget so a large
    backlog drains over several passes instead of holding the ``blob-gc``
    queue; each pass reads only one batch of metadata at a time (indexed
    ``created_time``), never the rows' content. A spec with no telemetry
    model registered (an in-memory monitor deploy) has nothing to purge."""
    try:
        rm = spec.get_resource_manager(TelemetryEvent)
    except KeyError:
        return 0
    started = clock()
    removed = 0
    query = (QB.created_time() < cutoff).sort(QB.created_time().asc()).limit(batch).build()
    while True:
        metas = rm.search_resources(query)
        for meta in metas:
            rm.permanently_delete(meta.resource_id)
        removed += len(metas)
        if len(metas) < batch or clock() - started >= budget_s:
            return removed


class SpecstarMonitor(IMonitor):
    def __init__(self, spec: SpecStar) -> None:
        super().__init__()
        # add_model is one-shot per spec; tolerate a second monitor over the
        # same spec (the events persist in the spec's storage either way).
        # group_id + seq are indexed so recent() can filter/sort/limit in-query.
        with contextlib.suppress(ValueError):
            spec.add_model(TelemetryEvent, indexed_fields=["group_id", "seq", "kind"])
        self._rm = spec.get_resource_manager(TelemetryEvent)
        # Resume the order key past the LARGEST one persisted — not the row
        # count: once old rows are purged (docs/plan-telemetry-retention.md)
        # the count falls below it, and a restart would hand out keys the kept
        # rows already hold, so `recent(limit=…)` would show old events.
        newest = self._rm.list_resources(QB.all().sort("-seq").limit(1).build())
        self._seq = self._payload_seq(newest[0]) if newest else 0

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
    def _payload_seq(r: Any) -> int:
        data = r.data
        assert isinstance(data, TelemetryEvent)  # narrow for ty (coverage-clean)
        return data.seq

    @staticmethod
    def _payload(r: Any) -> MonitorEvent:
        data = r.data
        assert isinstance(data, TelemetryEvent)  # narrow for ty (coverage-clean)
        return data.payload
