"""Which items the durable archive is refusing (docs/plan-archive-full.md P2).

A write-back the archive refused (a full disk, a dead mount) leaves the item's
newest work only in the sandbox host pod's own disk, which a rollout erases.
So the item must stop taking new turns until a write-back lands, and say why —
and the pod that ran the failing checkpoint is rarely the one a person's next
message lands on. Hence a shared row per item, like the activity heartbeat and
the sandbox address (`sandbox_activity.py`, `sandbox_address.py`): registered
post-`spec.apply`, so it has no CRUD routes of its own.

Two writers, both on `InvestigationRegistry`. `_writeback` — which every
checkpoint, turn-end reconcile and reap goes through — records a refusal, and
clears a record THIS pod made once one of its write-backs lands.
`recheck_archive`, asked by the turn gate before a turn would be refused,
clears any pod's record once the item's live sandbox writes back (or none is
live)."""

from __future__ import annotations

import abc
import asyncio
import contextlib
import datetime as dt
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
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
    with contextlib.suppress(ValueError):
        spec.add_model(_ArchiveLowEpisode)


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
    server, and a figure a few seconds old decides a 10% floor just as well.

    A mount that errors (a stale handle, a missing path) or hangs is not "the
    archive is low": the answer is None, said in the log, and nothing is refused
    on it — otherwise one bad mount turns every send on every item into a 500.
    At most ONE measurement is outstanding: a hung NFS `statvfs` cannot be
    cancelled, and a fresh thread per send would drain the executor every
    blocking call on this pod shares."""

    def __init__(
        self,
        root: Path | str,
        *,
        usage: Callable[[Path], Any] = shutil.disk_usage,
        clock: Callable[[], float] = time.monotonic,
        max_age_s: float = 30.0,
        timeout_s: float = 5.0,
    ) -> None:
        self.root = Path(root)
        self._usage = usage
        self._clock = clock
        self._max_age_s = max_age_s
        self._timeout_s = timeout_s
        self._last: tuple[float, int, int] | None = None
        # When the last measurement came back unknown: remembered as long as a
        # figure would be, so a hung mount costs ONE wait per `max_age_s`, not
        # one per send (review round 2).
        self._unknown_at: float | None = None
        self._inflight: asyncio.Future[Any] | None = None

    async def measure(self) -> tuple[int, int] | None:
        """`(free, total)` in bytes — what an unprivileged writer can still use —
        or None when it could not be measured just now."""
        now = self._clock()
        if self._last is not None and now - self._last[0] < self._max_age_s:
            return self._last[1], self._last[2]
        if self._unknown_at is not None and now - self._unknown_at < self._max_age_s:
            return None
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.ensure_future(asyncio.to_thread(self._usage, self.root))
        try:
            du = await asyncio.wait_for(asyncio.shield(self._inflight), self._timeout_s)
        except TimeoutError:
            logger.warning(
                "archive: could not measure %s: no answer within %gs", self.root, self._timeout_s
            )
            self._unknown_at = self._clock()
            return None
        except Exception as exc:  # noqa: BLE001 — any failure to measure is "unknown"
            logger.warning("archive: could not measure %s: %s", self.root, exc)
            self._unknown_at = self._clock()
            return None
        self._unknown_at = None
        self._last = (self._clock(), int(du.free), int(du.total))
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
        recheck: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.failures = failures
        self.space = space
        self.block_free_ratio = block_free_ratio
        # `InvestigationRegistry.recheck_archive`: one more write-back before a
        # recorded refusal stops a turn — the record can outlive what it
        # protects (a rollout leaves nobody to write it back and clear it).
        self._recheck = recheck

    async def check(self, item_id: str) -> None:
        if self.failures is not None and await self._reason(item_id):
            if self._recheck is not None:
                await self._recheck(item_id)
            if reason := await self._reason(item_id):
                raise ArchiveRefusing(item_id, reason)
        if self.space is None or self.block_free_ratio <= 0:
            return
        measured = await self.space.measure()
        if measured is None:
            return  # unknown is not low; `measure` said why in the log
        free, total = measured
        if total and free < total * self.block_free_ratio:
            raise ArchiveSpaceLow(free, total, self.block_free_ratio)

    async def _reason(self, item_id: str) -> str | None:
        """This item's recorded refusal, or None — including when the store cannot
        be read: every turn on every deploy asks, and a store hiccup must not turn
        each into a 500 (review round 2). Not knowing is not "refused"."""
        assert self.failures is not None
        try:
            return await self.failures.reason_of(item_id)
        except Exception:  # noqa: BLE001 — unknown is not refused; said in the log
            logger.warning(
                "archive: could not read the archive record of item %s", item_id, exc_info=True
            )
            return None


class _ArchiveLowEpisode(Struct):
    """One stretch below the warning floor: when it began, and since when the
    archive has been back above the floor (None = it is not). Every notification of
    the stretch is keyed by its start, so recovering and falling again is a new
    stretch, told afresh."""

    started_ms: int
    above_since_ms: int | None = None


_EPISODE_ID = "archive"

#: How long the archive must STAY above the warning floor before a stretch ends
#: (review round 1, D-4). rsync's writes and deletes nudge the free space either
#: side of the floor; ending on the first tick above it made every dip a fresh
#: "below 20%" to every superuser, and to their mail.
RECOVERED_AFTER_MS = 30 * 60 * 1000


def _levels(warn: float, block: float) -> list[float]:
    """The free-space fractions at which the superusers are told: the warning
    floor, halfway down, and the refusal floor (20%, 15%, 10% by default)."""
    if warn <= 0:
        return []
    raw = [warn, block]
    if 0 < block < warn:
        raw.append((warn + block) / 2)
    return sorted({round(level, 6) for level in raw if level > 0}, reverse=True)


def _gib(n: int) -> str:
    return f"{n / 1024**3:.1f} GiB"


class ArchiveSpaceWatch:
    """Tells every superuser, once per level crossed, that the archive is
    filling (docs/plan-archive-full.md D4) — so the people who can extend it hear
    before anyone is refused (D3).

    `notify()` writes the row the bell shows; the deploy's
    `server.notification_channel`, when wired, relays every row (mail, chat), so
    nothing here knows about mail (D5). Each row is keyed
    `archive-low:<stretch>:<level>:<who>` and checked with `notification_sent`
    first, so a second tick — or a second pod — does not tell it again; the
    lifespan's `ScanLease` keeps the pods from ticking at once in the first
    place."""

    def __init__(
        self,
        spec: SpecStar,
        space: ArchiveSpace,
        *,
        superusers: frozenset[str],
        warn_free_ratio: float,
        block_free_ratio: float,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._spec = spec
        self._space = space
        self._superusers = sorted(superusers)
        self._levels = _levels(warn_free_ratio, block_free_ratio)
        self._warn = warn_free_ratio
        self._block = block_free_ratio
        self._now_ms = now_ms or (lambda: int(dt.datetime.now(dt.UTC).timestamp() * 1000))

    async def tick(self) -> int:
        """Measure once and tell what is owed. Returns how many rows it wrote."""
        if not self._levels:
            return 0
        measured = await self._space.measure()
        if measured is None:
            return 0  # unknown: neither a new stretch nor the end of one
        free, total = measured
        if not total:
            return 0
        return await asyncio.to_thread(self._tick_sync, free, total)

    def _tick_sync(self, free: int, total: int) -> int:
        from .notifications import notification_sent, notify

        ratio = free / total
        if ratio >= self._warn:
            self._above_floor()
            return 0
        stretch = self._stretch()
        sent = 0
        for level in self._levels:
            if ratio >= level:
                continue
            for who in self._superusers:
                key = f"archive-low:{stretch}:{level:g}:{who}"
                if notification_sent(self._spec, key):
                    continue
                notify(
                    self._spec,
                    recipient=who,
                    kind="archive_space_low",
                    title=f"Sandbox archive is {100 * ratio:.1f}% free "
                    f"({_gib(free)} of {_gib(total)})",
                    body=self._body(ratio),
                    link="/capacity",
                    dedup_key=key,
                )
                sent += 1
        if sent:
            logger.warning(
                "archive: %.1f%% free (%d of %d bytes) under %s — told %d superuser row(s)",
                100 * ratio,
                free,
                total,
                self._space.root,
                sent,
            )
        return sent

    def _body(self, ratio: float) -> str:
        extend = f"Extend the volume behind {self._space.root}."
        if self._block > 0 and ratio < self._block:
            return (
                f"It is below the {self._block:.0%} floor: no item takes a new "
                f"conversation until it is extended. {extend}"
            )
        if self._block > 0:
            return f"Below {self._block:.0%} free, no item will take a new conversation. {extend}"
        return extend

    def _stretch(self) -> int:
        rm = self._spec.get_resource_manager(_ArchiveLowEpisode)
        try:
            data = rm.get(_EPISODE_ID).data
            assert isinstance(data, _ArchiveLowEpisode)
            if data.above_since_ms is not None:
                # Back below before it had stayed recovered: the same stretch.
                rm.modify(
                    _EPISODE_ID,
                    _ArchiveLowEpisode(started_ms=data.started_ms),
                    status=RevisionStatus.draft,
                )
            return data.started_ms
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            pass
        rec = _ArchiveLowEpisode(started_ms=self._now_ms())
        try:
            rm.restore(_EPISODE_ID)
            rm.modify(_EPISODE_ID, rec, status=RevisionStatus.draft)
        except ResourceIDNotFoundError:
            try:
                rm.create(rec, resource_id=_EPISODE_ID, status=RevisionStatus.draft)
            except DuplicateResourceError:
                # Another pod began the stretch first: its start is the key.
                data = rm.get(_EPISODE_ID).data
                assert isinstance(data, _ArchiveLowEpisode)
                return data.started_ms
        return rec.started_ms

    def _above_floor(self) -> None:
        """Above the warning floor this tick. The stretch ends only once it has
        stayed there for `RECOVERED_AFTER_MS`."""
        rm = self._spec.get_resource_manager(_ArchiveLowEpisode)
        try:
            data = rm.get(_EPISODE_ID).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return  # no stretch: nothing to end
        assert isinstance(data, _ArchiveLowEpisode)
        now = self._now_ms()
        if data.above_since_ms is None:
            rm.modify(
                _EPISODE_ID,
                _ArchiveLowEpisode(started_ms=data.started_ms, above_since_ms=now),
                status=RevisionStatus.draft,
            )
            return
        if now - data.above_since_ms >= RECOVERED_AFTER_MS:
            with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
                rm.delete(_EPISODE_ID)
                logger.info("archive: back above the warning floor")
