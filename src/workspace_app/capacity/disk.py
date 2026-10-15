"""Disk on the usage record (docs/plan-capacity-usage.md §6.6).

Written by the disk-ledger pass, which already visits every item on a worker
(the #804 convention: a whole-table walk is a job, never an API timer). Each
item's row is its ledger row after the reconcile — the bytes the quota charges,
to the person it charges them to — and each KB collection's is the sum of its
documents' `content_size`, the same grouped sum the KB page shows, to its
creator. A row is a snapshot: the analysis keeps each id's latest per day.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

from specstar import QB, SpecStar
from specstar.aggregates import ForeignAggregate, Sum

from .recorder import UsageRecorder
from .records import DiskRow


def kb_collection_bytes(spec: SpecStar) -> list[tuple[str, str, int]]:
    """`(collection_id, creator, bytes)` for every KB collection — one grouped
    sum in the database, not a read of every document. Blocking."""
    from ..resources import Collection, SourceDoc

    coll_rm = spec.get_resource_manager(Collection)
    doc_rm = spec.get_resource_manager(SourceDoc)
    rows = coll_rm.exp_aggregate_by(  # ty: ignore[unresolved-attribute]
        by=QB.resource_id(),
        aggregates={"size": ForeignAggregate(doc_rm, QB["collection_id"], Sum(QB["content_size"]))},
    )
    out = []
    for row in rows:
        res = row.resource
        out.append((res.info.resource_id, res.meta.created_by or "", int(row.size or 0)))
    return out


def emit_disk_rows(
    recorder: UsageRecorder,
    *,
    workspaces: Mapping[str, tuple[str, int]],
    app_of: Mapping[str, str],
    collections: list[tuple[str, str, int]],
) -> None:
    """One `disk` row per item (`workspaces`: item → (owner, bytes)) and per
    collection, all stamped with this pass's moment."""
    now = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    for item, (owner, size) in workspaces.items():
        row = DiskRow(
            scope="workspace", user=owner, app=app_of.get(item, ""), bytes=size, item=item
        )
        recorder.emit(row, at_ms=now)
    for collection, owner, size in collections:
        row = DiskRow(scope="kb", user=owner, app="", bytes=size, collection=collection)
        recorder.emit(row, at_ms=now)
