"""The ledger that lets the platform notice a backup that never happened.

A run that FAILS is visible — the CronJob goes red. The one nobody sees is the
run that never started: a suspended schedule, a cluster that lost it, a
destination nothing has written to since March. Absence produces no event, so
there is nothing to alert on.

So every completed run records itself here, and a sweeper reads the newest row.
Absence becomes a row that is too old, which is a thing you can look at.

The row is an internal coordination record, not an API resource: it self-registers
(like the sandbox-activity and sandbox-address rows) so the model exists for
every caller without auto-CRUD routes being emitted for it. That matters — a
world-writable `backup-run` table would let anyone forge freshness or delete the
evidence that a backup stopped.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
from typing import TYPE_CHECKING

from msgspec import Struct
from specstar import QB
from specstar.query_types import (
    ResourceMetaSearchQuery,
    ResourceMetaSearchSort,
    ResourceMetaSortDirection,
    ResourceMetaSortKey,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from specstar import SpecStar

    from .run import Receipt

logger = logging.getLogger(__name__)


class _BackupRun(Struct):  # → resource "-backuprun"
    """One completed run, as the platform sees it.

    `verified_blobs` rides along because "a run happened" and "a run proved its
    archives hold what they reference" are different claims, and an operator
    reading the ledger should be able to tell them apart.
    """

    run_id: str
    chain: str
    kind: str
    directory: str
    finished_at_ms: int
    verified_blobs: int
    bytes: int


class BackupLedger:
    """Read and write the completed-run ledger.

    Constructing one registers the model, idempotently — the same shape
    `SpecstarFileStore` uses, so any composition that touches backups has the
    model without a separate registration step to forget.
    """

    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec
        with contextlib.suppress(ValueError):
            spec.add_model(_BackupRun, indexed_fields=["chain", "kind"])

    def record(self, receipt: Receipt, *, now: dt.datetime | None = None) -> str:
        """Note that `receipt`'s run finished. Returns the row id."""
        stamp = now or dt.datetime.now(dt.UTC)
        rm = self._spec.get_resource_manager(_BackupRun)
        rev = rm.create(
            _BackupRun(
                run_id=receipt.run_id,
                chain=receipt.chain,
                kind=receipt.kind,
                directory=receipt.directory,
                finished_at_ms=int(stamp.timestamp() * 1000),
                verified_blobs=receipt.verified_blobs,
                bytes=sum(s.bytes for s in receipt.source_results),
            )
        )
        return rev.resource_id

    def newest(self) -> _BackupRun | None:
        """The most recently finished run, or None when there has never been one.

        Sorted by the row's own `updated_time` rather than by scanning: the
        ledger grows by one row per run forever, and a sweeper that reads all of
        them is the shape that took an API pod down once already (#804).
        """
        rm = self._spec.get_resource_manager(_BackupRun)
        query = ResourceMetaSearchQuery(
            limit=1,
            sorts=[
                ResourceMetaSearchSort(
                    key=ResourceMetaSortKey.updated_time,
                    direction=ResourceMetaSortDirection.descending,
                )
            ],
        )
        for row in rm.list_resources(query):
            data = row.data
            if isinstance(data, _BackupRun):
                return data
        return None

    def _age_newest_for_test(self, by: dt.timedelta) -> None:
        """Move the newest row's finish time backwards.

        A test seam, and deliberately a small one: ageing the ROW is what a real
        stall looks like, where freezing a clock would only prove the comparison
        reads the clock it was handed.
        """
        rm = self._spec.get_resource_manager(_BackupRun)
        for row in rm.list_resources(QB.all().build()):
            data = row.data
            if not isinstance(data, _BackupRun):  # pragma: no cover - one model, one type
                continue
            moved = data.finished_at_ms - int(by.total_seconds() * 1000)
            rid: str = row.info.resource_id  # ty: ignore[unresolved-attribute]
            rm.update(rid, _replace_finish(data, moved))


def register_backup_ledger(spec: SpecStar) -> None:
    """Register the ledger model on `spec`, idempotently.

    Called from `create_app` post-`spec.apply` so no CRUD routes are emitted and
    so the model is part of the composition a backup archives and a restore
    loads. Registering it only when a backup first runs would make an archive's
    contents depend on which code path ran first.
    """
    BackupLedger(spec)


def _replace_finish(row: _BackupRun, finished_at_ms: int) -> _BackupRun:
    return _BackupRun(
        run_id=row.run_id,
        chain=row.chain,
        kind=row.kind,
        directory=row.directory,
        finished_at_ms=finished_at_ms,
        verified_blobs=row.verified_blobs,
        bytes=row.bytes,
    )
