"""The per-person DISK ledger — the stock half of the resource limits.

Disk is nothing like cpu/memory here. It persists after every sandbox is gone,
so "how much is this person using?" cannot be derived from what is alive; it has
to be a durable sum over their workspaces. That is what this table is.

What it stores is a MEASUREMENT, not an authority. The size of a workspace is
whatever the live sandbox says it is (#538: the durable snapshot is deliberately
additive, so reading sizes from it both misses bytes the agent just created and
keeps charging for ones it deleted). This ledger only remembers the number the
measurement already produced, so it can be summed across items without walking
every workspace on every write.

The consequence is honest and deliberate: **a person's total trails reality by up
to one measurement**. The per-ITEM limit stays exact — it measures live on the
write path — so the looser number is only ever the cross-item one, and the worst
case is somebody briefly getting a little over their personal cap. Making it
exact would mean measuring every workspace a person owns on every write, which
is the traversal `WorkspaceFiles` exists to keep off the request path.

Charged to the item's `owner`, like everything else here (#687).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
from collections.abc import Callable

from msgspec import Struct
from specstar import QB, SpecStar
from specstar.types import (
    DuplicateResourceError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

logger = logging.getLogger(__name__)


class UserDiskFull(Exception):
    """A write was refused because its OWNER is at their personal disk total.

    Distinct from `WorkspaceFull` on purpose: the numbers a person needs to act
    on are different (their total across every item, not this workspace's), and
    so is the remedy — they may have to go and delete something in a completely
    different item."""

    def __init__(self, owner: str, used: int, quota: int, attempted: int) -> None:
        super().__init__(
            f"{owner} is out of space: {used} of {quota} bytes used across their "
            f"workspaces, cannot write {attempted} more"
        )
        self.owner = owner
        self.used = used
        self.quota = quota
        self.attempted = attempted


class _WorkspaceDisk(Struct):
    """One item's last measured size, and who owes for it. resource_id ==
    item_id, so a measurement is a point upsert; `owner` is indexed so the total
    is one scoped query rather than a walk over every item in the deploy."""

    item_id: str
    owner: str = ""
    bytes_used: int = 0
    measured_at_ms: int = 0


def register_disk_ledger(spec: SpecStar) -> None:
    """Idempotently register the ledger model. Call post-`spec.apply` so its CRUD
    routes are never emitted — it is internal accounting, not an API resource."""
    with contextlib.suppress(ValueError):
        spec.add_model(_WorkspaceDisk, indexed_fields=["owner"])


class DiskLedger:
    """Per-item measured sizes, summable per person.

    Blocking specstar I/O is offloaded to a thread, like every other store here,
    so a measurement landing never sits on the event loop."""

    def __init__(self, spec: SpecStar, *, now_ms: Callable[[], int] | None = None) -> None:
        self._spec = spec
        self._now_ms = now_ms

    def _now(self) -> int:
        if self._now_ms is not None:
            return self._now_ms()
        return int(dt.datetime.now(dt.UTC).timestamp() * 1000)

    async def record(self, item_id: str, owner: str, bytes_used: int) -> None:
        await asyncio.to_thread(self._record_sync, item_id, owner, bytes_used)

    def _record_sync(self, item_id: str, owner: str, bytes_used: int) -> None:
        rm = self._spec.get_resource_manager(_WorkspaceDisk)
        rec = _WorkspaceDisk(
            item_id=item_id,
            owner=owner,
            bytes_used=bytes_used,
            measured_at_ms=self._now(),
        )
        try:
            rm.modify(item_id, rec, status=RevisionStatus.draft)
            return
        except ResourceIDNotFoundError:
            pass
        except ResourceIsDeletedError:
            rm.restore(item_id)
            rm.modify(item_id, rec, status=RevisionStatus.draft)
            return
        with contextlib.suppress(DuplicateResourceError):
            rm.create(rec, resource_id=item_id, status=RevisionStatus.draft)

    async def forget(self, item_id: str) -> None:
        """Drop an item's row — it was deleted, so it owes nothing."""
        await asyncio.to_thread(self._forget_sync, item_id)

    def _forget_sync(self, item_id: str) -> None:
        rm = self._spec.get_resource_manager(_WorkspaceDisk)
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            rm.delete(item_id)

    async def total_for(self, owner: str, *, exclude: str = "") -> int:
        """Bytes this person's workspaces occupy, per the last measurement of
        each.

        `exclude` leaves one item out, because the caller has a LIVE measurement
        for the item being written to and must not count it twice — the stale
        row and the fresh number would otherwise both land in the sum."""
        return await asyncio.to_thread(self._total_sync, owner, exclude)

    async def per_item_for(self, owner: str) -> list[tuple[str, int]]:
        """`(item_id, bytes)` for each of this person's measured workspaces —
        what the usage panel lists so somebody can see WHERE their space went.
        A total alone tells you that you are full, not what to delete."""
        return await asyncio.to_thread(self._per_item_sync, owner)

    def _per_item_sync(self, owner: str) -> list[tuple[str, int]]:
        rm = self._spec.get_resource_manager(_WorkspaceDisk)
        query = ((QB["owner"] == owner) & (QB.is_deleted() == False)).build()  # noqa: E712
        out: list[tuple[str, int]] = []
        for rev in rm.list_resources(query):
            data = rev.data
            assert isinstance(data, _WorkspaceDisk)
            out.append((data.item_id, data.bytes_used))
        return out

    def _total_sync(self, owner: str, exclude: str) -> int:
        # `is_deleted == False` for the same reason the sandbox ledger needs it:
        # `list_resources` returns soft-deleted rows, so a forgotten item would
        # keep charging its owner forever.
        rm = self._spec.get_resource_manager(_WorkspaceDisk)
        query = ((QB["owner"] == owner) & (QB.is_deleted() == False)).build()  # noqa: E712
        total = 0
        for rev in rm.list_resources(query):
            data = rev.data
            assert isinstance(data, _WorkspaceDisk)
            if data.item_id != exclude:
                total += data.bytes_used
        return total
