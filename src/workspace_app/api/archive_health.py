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
from collections.abc import Callable

from msgspec import Struct
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

logger = logging.getLogger(__name__)


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
