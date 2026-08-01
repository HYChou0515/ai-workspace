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
from specstar import QB, SpecStar
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
    async def bump(
        self,
        item_id: str,
        *,
        owner: str = "",
        cpu_milli: int = 0,
        memory_bytes: int = 0,
    ) -> None:
        """Record that ``item_id`` was just active (now).

        The extra fields make this row double as the per-person cpu/memory
        ledger: ``owner`` is who the live sandbox is charged to, and the two
        amounts are what that sandbox is allowed to consume. They ride the
        heartbeat rather than living in a second table because a separate
        ledger would need its own liveness rule, and two liveness rules for one
        sandbox is how a quota starts lying."""

    @abc.abstractmethod
    async def last_active_ms(self, item_id: str) -> int | None:
        """Epoch-ms of the item's last recorded activity, or None if unknown."""

    @abc.abstractmethod
    async def forget(self, item_id: str) -> None:
        """Drop the heartbeat (the item's dir was recycled / closed)."""

    @abc.abstractmethod
    async def live_for(self, owner: str, *, since_ms: int) -> list[LiveSandbox]:
        """Every sandbox charged to ``owner`` whose heartbeat is at least as
        recent as ``since_ms``.

        The time window is what makes this tally SELF-HEALING. Nothing ever
        decrements: a sandbox that is reaped normally has its row forgotten, and
        one whose pod died without reaping simply stops being returned once its
        heartbeat ages past the window. A counter incremented on create and
        decremented on kill would leak a slot on every missed decrement, and the
        person would be locked out holding zero live sandboxes.

        The window should be the reaper's idle threshold: shorter under-counts a
        live-but-idle sandbox that is still holding memory, longer keeps
        charging for one already reclaimed."""


class _SandboxActivity(Struct):
    """One item's last-activity heartbeat, and what its live sandbox costs.

    resource_id == item_id, so every pod upserts/reads the one shared row by a
    point key (no scan). ``owner`` is indexed so the admission gate can ask "how
    much is this person holding right now?" without walking every item."""

    item_id: str
    last_active_ms: int = 0
    # The debtor. See #687: this mirrors the item's `owner` FIELD, which is
    # today rewritable by anyone with write access — the reason the per-person
    # limits are not yet tamper-proof.
    owner: str = ""
    # What this item's sandbox may consume, from its App (`quota.limits`).
    # Milli-cores rather than a float so the index sorts and sums exactly.
    cpu_milli: int = 0
    memory_bytes: int = 0


class LiveSandbox(Struct):
    """One live sandbox charged to a person — what the admission gate counts."""

    item_id: str
    cpu_milli: int = 0
    memory_bytes: int = 0


def register_sandbox_activity(spec: SpecStar) -> None:
    """Idempotently register the heartbeat model. Safe to call on every pod.

    ``owner`` is indexed because the gate FILTERS on it. A missed backfill on a
    filtered index is not a wrong number, it is missing rows — an unmigrated row
    answers no `owner` predicate, so a person's usage would read as zero and the
    limit would not bind. These rows are short-lived leases that age out on their
    own, so no migrate step is needed here; the same is NOT true of the item
    models (see the deploy note in docs/plan-sandbox-resource-quota.md)."""
    with contextlib.suppress(ValueError):
        spec.add_model(_SandboxActivity, indexed_fields=["owner", "last_active_ms"])


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

    async def bump(
        self,
        item_id: str,
        *,
        owner: str = "",
        cpu_milli: int = 0,
        memory_bytes: int = 0,
    ) -> None:
        await asyncio.to_thread(self._bump_sync, item_id, owner, cpu_milli, memory_bytes)

    def _bump_sync(self, item_id: str, owner: str, cpu_milli: int, memory_bytes: int) -> None:
        rm = self._spec.get_resource_manager(_SandboxActivity)
        rec = _SandboxActivity(
            item_id=item_id,
            last_active_ms=self._now(),
            owner=owner,
            cpu_milli=cpu_milli,
            memory_bytes=memory_bytes,
        )
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

    async def live_for(self, owner: str, *, since_ms: int) -> list[LiveSandbox]:
        return await asyncio.to_thread(self._live_sync, owner, since_ms)

    def _live_sync(self, owner: str, since_ms: int) -> list[LiveSandbox]:
        # Scoped by the indexed `owner` — never a global scan to answer a
        # question about one person. The time bound is applied here rather than
        # in the query so the predicate stays a single equality on the index;
        # the result set is one person's live sandboxes, which is small by
        # construction (that is the very thing being capped).
        # `is_deleted == False` is NOT optional: `forget` soft-deletes, and
        # `list_resources` happily returns soft-deleted rows. Without it a reaped
        # sandbox would keep occupying its owner's quota forever — the exact
        # never-gives-the-slot-back failure this ledger exists to avoid.
        rm = self._spec.get_resource_manager(_SandboxActivity)
        query = ((QB["owner"] == owner) & (QB.is_deleted() == False)).build()  # noqa: E712
        out: list[LiveSandbox] = []
        for rev in rm.list_resources(query):
            data = rev.data
            assert isinstance(data, _SandboxActivity)
            if data.last_active_ms >= since_ms:
                out.append(
                    LiveSandbox(
                        item_id=data.item_id,
                        cpu_milli=data.cpu_milli,
                        memory_bytes=data.memory_bytes,
                    )
                )
        return out
