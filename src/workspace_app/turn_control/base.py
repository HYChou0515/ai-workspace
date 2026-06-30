"""ITurnControl (#349) — the cross-pod turn-cancel signal.

Turn cancellation used to live entirely in one pod's memory (an `asyncio.Task`
handle on `ChatTurnEngine`), so a new message / Stop that landed on a *different*
replica than the one running the turn could not cancel it (#345 downgraded nginx
sticky routing from a correctness mechanism to a warm-cache optimisation, which
exposes this).

The fix is a single monotonic ``epoch`` per turn-engine key, shared across pods.
A running turn stamps the epoch it started at; a watcher polls ``current`` and
aborts the turn once the epoch has advanced past that stamp. ``advance`` is how a
new turn supersedes a prior one — or how an explicit Stop kills the running one
without touching a later, legitimately-new turn (it stamps a higher epoch). The
"supersede vs serialise" difference is entirely in the *caller*: a superseding
start calls ``advance`` (bump-then-stamp) while a serialising start stamps
``current`` (read, no bump).

specstar has no cross-process pub/sub, so the contract is poll-friendly: cheap
``current`` reads, an atomic ``advance``. Backends are swappable
(``InMemoryTurnControl`` for tests / single-pod, a specstar-backed impl for
multi-pod) exactly like the other pluggable layers.
"""

from __future__ import annotations

import abc


class ITurnControl(abc.ABC):
    """A monotonic per-key epoch, shared across pods, that gates turn liveness."""

    @abc.abstractmethod
    async def current(self, key: str) -> int:
        """The latest epoch for ``key`` — ``0`` if no turn has ever started.

        Polled by a running turn's watcher: the turn aborts once this exceeds the
        epoch it stamped at start."""
        ...

    @abc.abstractmethod
    async def advance(self, key: str) -> int:
        """Atomically bump ``key``'s epoch and return the new value.

        Called when a turn must supersede whatever ran before it (a new KB-chat
        message) or to interrupt the in-flight turn (an explicit Stop)."""
        ...
