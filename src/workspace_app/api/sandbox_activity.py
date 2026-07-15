"""Global sandbox-activity heartbeat (#345).

With a shared per-item working dir on one volume, ``InvestigationRegistry`` runs
on every API pod but only sees ITS OWN sessions. The idle reaper must NOT tear
down (``rmtree``) a shared dir just because THIS pod went idle on it — another
pod may still be serving the same item. So "is this item idle?" has to be a
GLOBAL question.

This stores a per-item ``last_active_ms`` heartbeat in the shared backend
(specstar), bumped by whichever pod last woke/used the sandbox. The reaper reads
it and only recycles a dir when no pod has touched it past the idle threshold.

Recycle stays lease-free on purpose: the heartbeat gate already keeps a live dir
from being reclaimed, and the recycle steps are idempotent + non-destructive
(``mirror`` writes the durable snapshot BEFORE the ``rmtree``, blobs are
content-addressed, ``rmtree``/``forget`` are idempotent), so a rare double
recycle by two pods archives the same bytes and removes the same dir — never a
data loss. The model self-registers (like the #245 blob-GC lease) so the
memory-default app doesn't emit its CRUD routes.
"""

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


class IActivityStore(abc.ABC):
    """Per-item activity heartbeat shared across pods. ``None`` everywhere it's
    optional means "no global signal" — callers then fall back to single-process
    (pod-local) behaviour."""

    @abc.abstractmethod
    async def bump(self, item_id: str) -> None:
        """Record that ``item_id`` was just active (now)."""

    @abc.abstractmethod
    async def last_active_ms(self, item_id: str) -> int | None:
        """Epoch-ms of the item's last recorded activity, or None if unknown."""

    @abc.abstractmethod
    async def forget(self, item_id: str) -> None:
        """Drop the heartbeat (the item's dir was recycled / closed)."""


class _SandboxActivity(Struct):
    """One item's last-activity heartbeat. resource_id == item_id, so every pod
    upserts/reads the one shared row by a point key (no scan)."""

    item_id: str
    last_active_ms: int = 0


def register_sandbox_activity(spec: SpecStar) -> None:
    """Idempotently register the heartbeat model. Safe to call on every pod."""
    with contextlib.suppress(ValueError):
        spec.add_model(_SandboxActivity)


class SpecstarActivityStore(IActivityStore):
    """``IActivityStore`` over a shared specstar backend. Blocking specstar I/O
    is offloaded to a thread so it never sits on the event loop, mirroring the
    rest of the app's specstar access."""

    def __init__(self, spec: SpecStar, *, now_ms: Callable[[], int] | None = None) -> None:
        self._spec = spec
        self._now_ms = now_ms  # injectable clock for deterministic tests

    def _now(self) -> int:
        if self._now_ms is not None:
            return self._now_ms()
        return int(dt.datetime.now(dt.UTC).timestamp() * 1000)

    async def bump(self, item_id: str) -> None:
        await asyncio.to_thread(self._bump_sync, item_id)

    def _bump_sync(self, item_id: str) -> None:
        rm = self._spec.get_resource_manager(_SandboxActivity)
        rec = _SandboxActivity(item_id=item_id, last_active_ms=self._now())
        logger.debug("activity: bump heartbeat item=%s ms=%d", item_id, rec.last_active_ms)
        try:
            rm.modify(item_id, rec, status=RevisionStatus.draft)
            return
        except ResourceIDNotFoundError:
            logger.debug("activity: item %s heartbeat row absent, creating fresh", item_id)
        except ResourceIsDeletedError:
            logger.debug("activity: item %s reactivated, restoring heartbeat row", item_id)
            # A previously-forgotten (soft-deleted) item became active again —
            # restore the row, then stamp the fresh time.
            rm.restore(item_id)
            rm.modify(item_id, rec, status=RevisionStatus.draft)
            return
        with contextlib.suppress(DuplicateResourceError):
            rm.create(rec, resource_id=item_id, status=RevisionStatus.draft)

    async def last_active_ms(self, item_id: str) -> int | None:
        return await asyncio.to_thread(self._read_sync, item_id)

    def _read_sync(self, item_id: str) -> int | None:
        rm = self._spec.get_resource_manager(_SandboxActivity)
        try:
            res = rm.get(item_id)
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None  # unknown OR forgotten → no live heartbeat
        data = res.data
        assert isinstance(data, _SandboxActivity)
        return data.last_active_ms

    async def forget(self, item_id: str) -> None:
        await asyncio.to_thread(self._forget_sync, item_id)

    def _forget_sync(self, item_id: str) -> None:
        rm = self._spec.get_resource_manager(_SandboxActivity)
        logger.debug("activity: forget heartbeat for item %s", item_id)
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            rm.delete(item_id)
