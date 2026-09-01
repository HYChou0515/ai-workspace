"""Is anyone actually running this turn? (cross-pod)

A viewer watching a chat cannot answer that today, and every notice built on the
question had to guess. The live stream is per-pod — a viewer subscribed to a
replica that is not running the turn hears nothing, and silence there proves
nothing. The persisted thread is written at turn END, so mid-turn it says
exactly what a turn nobody is running says: the thread ends on the question.
"Producing nothing yet" and "lost" are the same picture, so a screen that
insists on a verdict is choosing which way to be wrong: claim it is running and
the user waits forever for nobody, or claim it is gone and offer to re-ask a
turn that is quietly working.

So record the fact instead of inferring it, in the shape this repo already uses
for the same class of question (`sandbox_activity`, #345): a heartbeat row in
the shared backend, written by whichever pod is driving the turn and readable
from any of them.

The heartbeat has to come from a TIMER, not from the turn's own events. The case
that matters most is a turn that produces nothing for minutes — a long tool call,
a slow first token — and an event-driven bump goes quiet at exactly the moment
its answer is needed.

Ageing out is the design, not a fallback. A pod that dies mid-turn cannot clear
a flag, and nothing else knows it should: a row that had to be deleted to mean
"finished" would say "running" forever after a crash, which is precisely the
state this exists to end. A heartbeat stops on its own.
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

#: How long after the last beat a turn counts as gone. Several beats wide, so a
#: slow store write or a busy loop never reads as a death; short enough that a
#: person waiting on a dead turn is told within a beat or two of the truth.
TURN_STALE_AFTER_MS = 30_000

#: How often a running turn says it is still there. A point write per interval
#: per in-flight turn — next to a model call, free.
TURN_HEARTBEAT_MS = 5_000


class ITurnActivityStore(abc.ABC):
    """Whether a turn is being driven right now, answerable from any pod."""

    @abc.abstractmethod
    async def bump(self, key: str) -> None:
        """Record that the turn on ``key`` is being driven, as of now."""

    @abc.abstractmethod
    async def finished(self, key: str) -> None:
        """The turn ended. Idempotent: a turn can end by answering, erroring or
        being cancelled, and more than one of those can try."""

    @abc.abstractmethod
    async def alive(self, key: str, *, stale_after_ms: int = TURN_STALE_AFTER_MS) -> bool:
        """Whether a turn on ``key`` has beaten within ``stale_after_ms``.

        `False` covers all three ways of not being alive — never started,
        finished, or the pod driving it died — because to the person waiting
        they are the same thing: nobody is coming."""


class _TurnActivity(Struct):
    """One chat's in-flight-turn heartbeat.

    `resource_id == key`, so every pod reads the one shared row by point key. No
    index and no scan: the only question ever asked is about one chat."""

    key: str
    last_beat_ms: int = 0


def register_turn_activity(spec: SpecStar) -> None:
    """Idempotently register the heartbeat model. Safe on every pod.

    Unindexed on purpose — this is a point lookup by id, and an index would be a
    filtered one, which is the kind a missed backfill turns into missing rows
    rather than a wrong number."""
    with contextlib.suppress(ValueError):
        spec.add_model(_TurnActivity)


class SpecstarTurnActivityStore(ITurnActivityStore):
    """`ITurnActivityStore` over the shared specstar backend. Blocking specstar
    I/O is offloaded to a thread, like the rest of the app's specstar access."""

    def __init__(self, spec: SpecStar, *, now_ms: Callable[[], int] | None = None) -> None:
        self._spec = spec
        self._now_ms = now_ms  # injectable clock for deterministic tests

    def _now(self) -> int:
        if self._now_ms is not None:
            return self._now_ms()
        return int(dt.datetime.now(dt.UTC).timestamp() * 1000)

    async def bump(self, key: str) -> None:
        await asyncio.to_thread(self._bump_sync, key)

    def _bump_sync(self, key: str) -> None:
        rm = self._spec.get_resource_manager(_TurnActivity)
        rec = _TurnActivity(key=key, last_beat_ms=self._now())
        try:
            rm.modify(key, rec, status=RevisionStatus.draft)
            return
        except ResourceIDNotFoundError:
            pass
        except ResourceIsDeletedError:
            # The previous turn on this chat finished and soft-deleted the row.
            # A new turn has to bring it back, or a second question would look
            # abandoned from its first second.
            rm.restore(key)
            rm.modify(key, rec, status=RevisionStatus.draft)
            return
        with contextlib.suppress(DuplicateResourceError):
            rm.create(rec, resource_id=key, status=RevisionStatus.draft)

    async def finished(self, key: str) -> None:
        await asyncio.to_thread(self._finished_sync, key)

    def _finished_sync(self, key: str) -> None:
        rm = self._spec.get_resource_manager(_TurnActivity)
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            rm.delete(key)

    async def alive(self, key: str, *, stale_after_ms: int = TURN_STALE_AFTER_MS) -> bool:
        return await asyncio.to_thread(self._alive_sync, key, stale_after_ms)

    def _alive_sync(self, key: str, stale_after_ms: int) -> bool:
        rm = self._spec.get_resource_manager(_TurnActivity)
        try:
            res = rm.get(key)
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return False  # never started, or finished
        data = res.data
        assert isinstance(data, _TurnActivity)
        return self._now() - data.last_beat_ms < stale_after_ms
