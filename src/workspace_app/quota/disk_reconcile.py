"""Keep the per-person disk ledger complete (docs/plan-storage-all-items.md
decision 5).

The ledger is written by live activity only — a growing write through the file
facade, a delete, the mirror sweep of a pod's warm sandboxes. An item nobody
has touched since the ledger shipped has no row, so it was neither listed on
"我的資源" nor counted toward its owner's total; an item whose sandbox was reaped
kept whatever was last measured while it was live.

This pass looks at every item that exists and sets its row right:

* an item with no live sandbox gets its DURABLE size — after a reap the durable
  copy has the deletions written back, so it is the item's real size; it is the
  source the per-item quota already uses for a cold item;
* a live item is left to the mirror — its durable copy is additive while it is
  live (#538), so the mirror's measurement is the honest one — and only its
  debtor is corrected if the item moved;
* a row whose item no longer exists is dropped — but only on `exists`'s word,
  never because the listing missed it. A SOFT-deleted item still exists here:
  its files stay until the cascade purges them, and the ledger's live writers
  keep charging it (`include_deleted=True` behind `_owner_of`). An App this
  process does not register is not this module's guard — the blob-gc job's
  registry check refuses a runner that lacks any model the asker has;
* a row already right is not written again.

One item's failure costs that item only. Pure over what it is handed, so the
job (`filestore.blob_gc`, kind `disk-ledger`) and the tests drive the same code.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from specstar import QB, SpecStar

from .disk_ledger import DiskLedger

if TYPE_CHECKING:
    from ..capacity.recorder import UsageRecorder
    from ..filestore.protocol import FileStore

logger = logging.getLogger(__name__)


@dataclass
class DiskReconcileReport:
    recorded: int = 0
    forgotten: int = 0
    skipped_live: int = 0
    failed: int = 0


async def reconcile_disk_ledger(
    *,
    items: Iterable[tuple[str, str]],
    is_live: Callable[[str], Awaitable[bool]],
    usage: Callable[[str], Awaitable[int]],
    exists: Callable[[str], bool],
    ledger: DiskLedger,
) -> DiskReconcileReport:
    """``items`` is ``(item_id, debtor)`` for every item that exists — the debtor
    by the quota gate's own rule (`apps.resolve.debtor_of`)."""
    report = DiskReconcileReport()
    rows = await ledger.rows()
    seen: set[str] = set()
    for item_id, owner in items:
        seen.add(item_id)
        owner = owner.strip()
        if not owner:
            # `debtor_of` floors to the creator; an item nobody owes for is not
            # booked to nobody (the gate refuses the same).
            continue
        row = rows.get(item_id)
        try:
            if await is_live(item_id):
                report.skipped_live += 1
                if row is not None and row.owner != owner:
                    await ledger.record(item_id, owner, row.bytes_used)
                    report.recorded += 1
                continue
            size = await usage(item_id)
            if row is not None and row.owner == owner and row.bytes_used == size:
                continue
            await ledger.record(item_id, owner, size)
            report.recorded += 1
        except Exception:  # noqa: BLE001 — one item must not stop the pass
            logger.exception("disk ledger reconcile: item %s could not be measured", item_id)
            report.failed += 1
    for item_id in rows.keys() - seen:
        if exists(item_id):
            continue
        await ledger.forget(item_id)
        report.forgotten += 1
    return report


def every_item(spec: SpecStar) -> list[tuple[str, str]]:
    """``(item_id, debtor)`` for every item — `every_item_with_app` without the App."""
    return [(item, debtor) for item, debtor, _slug in every_item_with_app(spec)]


def every_item_with_app(spec: SpecStar) -> list[tuple[str, str, str]]:
    """``(item_id, debtor, app slug)`` for every item of every App this process registers,
    soft-deleted ones included (they still hold their files, and the gate still
    charges them) — the debtor by the quota gate's own rule
    (`apps.resolve.debtor_of`). A
    whole-table read per App: run on a worker (the CLAUDE.md sweeper rule), never
    on an API pod's timer. Blocking."""
    from ..apps.base import WorkItemBase
    from ..apps.registry import registered_apps
    from ..apps.resolve import debtor_of

    out: list[tuple[str, str, str]] = []
    for slug, model in registered_apps().items():
        try:
            rm = spec.get_resource_manager(model)
        except Exception:  # noqa: BLE001 — an App this spec does not hold
            continue
        # No `is_deleted` filter: `list_resources` returns soft-deleted rows too,
        # and here that is wanted.
        for rev in rm.list_resources(QB.all().build()):
            item_id = rev.info.resource_id  # ty: ignore[unresolved-attribute]
            data = rev.data
            assert isinstance(data, WorkItemBase)  # narrow for ty
            out.append((item_id, debtor_of(spec, slug, item_id, data), slug))
    return out


def make_disk_ledger_pass(
    spec: SpecStar,
    filestore: FileStore,
    *,
    live_window_ms: int,
    usage_recorder: UsageRecorder | None = None,
) -> Callable[[], Coroutine[Any, Any, DiskReconcileReport]] | None:
    """The pass as the job runs it: every item, liveness from the sandbox
    heartbeat (a sandbox active within the idle window is live; a reaped one's
    row is gone), the durable store's size, existence by the one item lookup.

    None for a store with no usage accounting (`workspace_usage` is a
    duck-typed capability — `filestore/protocol.py`): a pass there would book
    every item at 0 bytes, which is worse than no pass."""
    workspace_usage = getattr(filestore, "workspace_usage", None)
    if workspace_usage is None:
        return None
    from ..api.sandbox_activity import SpecstarActivityStore
    from ..apps.resolve import find_work_item

    ledger = DiskLedger(spec)
    activity = SpecstarActivityStore(spec)

    async def run() -> DiskReconcileReport:
        reachable = getattr(filestore, "reachable", None)
        if reachable is not None and not await asyncio.to_thread(reachable):
            # A worker without the durable mount reads every tree as empty —
            # booking 0 bytes for every cold item would wipe the real figures.
            raise RuntimeError(
                "disk-ledger: the durable store's tree is not reachable from this "
                "process — mount it here (kubernetes/base/workers.yaml, blob-gc)"
            )
        items = await asyncio.to_thread(every_item_with_app, spec)
        since_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000) - live_window_ms

        async def is_live(item_id: str) -> bool:
            return await activity.is_live(item_id, since_ms=since_ms)

        report = await reconcile_disk_ledger(
            items=[(item, debtor) for item, debtor, _slug in items],
            is_live=is_live,
            usage=workspace_usage,
            exists=lambda item_id: find_work_item(spec, item_id, include_deleted=True) is not None,
            ledger=ledger,
        )
        if usage_recorder is not None:
            # docs/plan-capacity-usage.md §6.6: the reconciled figures, as rows.
            # Telemetry after the pass's real work: it must not fail a pass
            # that reconciled (the blob-gc job is not retried).
            from ..capacity.disk import emit_disk_rows, kb_collection_bytes

            try:
                rows = await ledger.rows()
                emit_disk_rows(
                    usage_recorder,
                    workspaces={item: (row.owner, row.bytes_used) for item, row in rows.items()},
                    app_of={item: slug for item, _debtor, slug in items},
                    collections=await asyncio.to_thread(kb_collection_bytes, spec),
                )
            except Exception:
                logger.exception("disk-ledger: reconciled, but could not write the usage rows")
        return report

    return run
