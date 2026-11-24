"""Which items the durable archive is refusing (docs/plan-archive-full.md P2).

A write-back the archive refused (a full disk, a dead mount) leaves the item's
newest work only in the sandbox host pod's own disk, which a rollout erases.
So the item must stop taking new turns until a write-back lands, and say why —
and the pod that ran the failing checkpoint is rarely the one a person's next
message lands on. Hence a shared row per item, like the activity heartbeat and
the sandbox address (`sandbox_activity.py`, `sandbox_address.py`): registered
post-`spec.apply`, so it has no CRUD routes of its own.

One writer: `InvestigationRegistry._writeback`, which every checkpoint, turn-end
reconcile and reap goes through. A refusal records the reason; any write-back
that lands clears it, whichever pod made it."""

from __future__ import annotations

import abc
import asyncio
import contextlib
import datetime as dt
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from msgspec import Struct
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

logger = logging.getLogger(__name__)


class ArchiveRefusing(Exception):
    """A turn refused because the archive is refusing this item's write-backs
    (D1): its newest work exists only on the sandbox host pod's own disk, and a
    new turn would add more that a rollout erases. `reason` is the archive's own
    words (rsync's), shown as they are (D2)."""

    code = "archive_write_failed"

    def __init__(self, item_id: str, reason: str) -> None:
        self.item_id = item_id
        self.reason = reason
        super().__init__(f"saving to the archive is failing for this item: {reason}")


class IArchiveFailures(abc.ABC):
    """Per-item "the last write-back was refused, and this is why"."""

    @abc.abstractmethod
    async def record(self, item_id: str, reason: str) -> None:
        """The archive refused this item's write-back, saying `reason`."""

    @abc.abstractmethod
    async def clear(self, item_id: str) -> None:
        """A write-back of this item landed: nothing is owed any more."""

    @abc.abstractmethod
    async def reason_of(self, item_id: str) -> str | None:
        """Why this item's last write-back was refused; None when it landed."""


class _ArchiveWriteFailure(Struct):
    item_id: str
    reason: str
    at_ms: int


def register_archive_health(spec: SpecStar) -> None:
    """Idempotently register the model. Safe to call on every pod; nothing is
    indexed — the only read is by id."""
    with contextlib.suppress(ValueError):
        spec.add_model(_ArchiveWriteFailure)


class SpecstarArchiveFailures(IArchiveFailures):
    """`IArchiveFailures` over the shared specstar backend, off the event loop."""

    def __init__(self, spec: SpecStar, *, now_ms: Callable[[], int] | None = None) -> None:
        self._spec = spec
        self._now_ms = now_ms

    def _now(self) -> int:
        if self._now_ms is not None:
            return self._now_ms()
        return int(dt.datetime.now(dt.UTC).timestamp() * 1000)

    async def record(self, item_id: str, reason: str) -> None:
        await asyncio.to_thread(self._record_sync, item_id, reason)

    def _record_sync(self, item_id: str, reason: str) -> None:
        rm = self._spec.get_resource_manager(_ArchiveWriteFailure)
        rec = _ArchiveWriteFailure(item_id=item_id, reason=reason, at_ms=self._now())
        try:
            rm.modify(item_id, rec, status=RevisionStatus.draft)
            return
        except ResourceIDNotFoundError:
            pass
        except ResourceIsDeletedError:
            # Cleared before, refused again: the same row comes back.
            rm.restore(item_id)
            rm.modify(item_id, rec, status=RevisionStatus.draft)
            return
        with contextlib.suppress(DuplicateResourceError):
            rm.create(rec, resource_id=item_id, status=RevisionStatus.draft)

    async def clear(self, item_id: str) -> None:
        await asyncio.to_thread(self._clear_sync, item_id)

    def _clear_sync(self, item_id: str) -> None:
        rm = self._spec.get_resource_manager(_ArchiveWriteFailure)
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            rm.delete(item_id)
            logger.info("archive: item %s writes back again", item_id)

    async def reason_of(self, item_id: str) -> str | None:
        return await asyncio.to_thread(self._reason_sync, item_id)

    def _reason_sync(self, item_id: str) -> str | None:
        rm = self._spec.get_resource_manager(_ArchiveWriteFailure)
        try:
            data = rm.get(item_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        assert isinstance(data, _ArchiveWriteFailure)
        return data.reason


class ArchiveSpace:
    """How much room the archive has left, as THIS pod mounts it (D3, D8).

    The archive is the claim the sandbox host rsyncs into, and the app pods
    mount the same claim (`sandbox.durable.nfs_root`), so the answer is one
    `disk_usage` call here — no host↔app protocol to drift. Cached for
    `max_age_s`: a burst of sends must not each make a round trip to the NFS
    server, and a figure a few seconds old decides a 10% floor just as well."""

    def __init__(
        self,
        root: Path | str,
        *,
        usage: Callable[[Path], Any] = shutil.disk_usage,
        clock: Callable[[], float] = time.monotonic,
        max_age_s: float = 30.0,
    ) -> None:
        self.root = Path(root)
        self._usage = usage
        self._clock = clock
        self._max_age_s = max_age_s
        self._last: tuple[float, int, int] | None = None

    async def measure(self) -> tuple[int, int]:
        """`(free, total)` in bytes — what an unprivileged writer can still use."""
        now = self._clock()
        if self._last is not None and now - self._last[0] < self._max_age_s:
            return self._last[1], self._last[2]
        du = await asyncio.to_thread(self._usage, self.root)
        self._last = (now, int(du.free), int(du.total))
        return int(du.free), int(du.total)


class ArchiveSpaceLow(Exception):
    """A turn refused because the archive is below its floor (D3): every item
    stops taking new turns BEFORE the archive is full, not after, so nobody's
    work is left with nowhere to go. The numbers are the reason."""

    code = "archive_space_low"

    def __init__(self, free: int, total: int, block_free_ratio: float) -> None:
        self.free = free
        self.total = total
        self.block_free_ratio = block_free_ratio
        pct = 100 * free / total if total else 0
        super().__init__(
            f"the sandbox archive has {free} of {total} bytes free ({pct:.1f}%), "
            f"below the {block_free_ratio:.0%} floor"
        )


class ArchiveGate:
    """The two archive refusals a turn can meet, asked in one place by both turn
    gates (the chat send and a workflow step — `turn_gate.admit_turn`).

    This item's own refusal comes first: it carries the archive's reason, which
    says more than a percentage. `space` None ⇒ no archive on this deploy (D8);
    a floor of 0 switches its refusal off (D7)."""

    def __init__(
        self,
        failures: IArchiveFailures | None,
        space: ArchiveSpace | None,
        *,
        block_free_ratio: float,
    ) -> None:
        self.failures = failures
        self.space = space
        self.block_free_ratio = block_free_ratio

    async def check(self, item_id: str) -> None:
        if self.failures is not None and (reason := await self.failures.reason_of(item_id)):
            raise ArchiveRefusing(item_id, reason)
        if self.space is None or self.block_free_ratio <= 0:
            return
        free, total = await self.space.measure()
        if total and free < total * self.block_free_ratio:
            raise ArchiveSpaceLow(free, total, self.block_free_ratio)
