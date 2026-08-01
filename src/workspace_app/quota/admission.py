"""Admission control for NEW sandboxes — the per-person cpu/memory half.

cpu and memory are flows: they exist only while a sandbox is alive and are
reclaimed when it is reaped. So there is nothing to check at write time and
nothing durable to keep. The only moment that matters is the one where a NEW
sandbox would be created, and the only question is "how much is this person
holding right now?".

Three properties this must have, each of which was a decision:

- **Only new sandboxes are gated.** An item that already has a live sandbox is
  already holding its slot; refusing it would stop someone using what they have
  open, which is not what any limit is for.
- **The tally is derived, never counted.** It comes from live heartbeats inside
  a window (`IActivityStore.live_for`), so a sandbox whose pod died without
  reaping stops counting on its own. An incremented counter leaks a slot per
  missed decrement, and the person ends up locked out holding nothing.
- **Refusal happens before the turn is persisted.** The caller gates at the
  service boundary, not where the agent first reaches for `exec` — otherwise a
  turn is spent rediscovering a limit we already knew about.

The debtor is the item's `owner`. See #687: that field is currently rewritable
by anyone with write access, so these limits bind only as far as that issue's
lockdown. This module does not paper over it — it charges the owner, and #687
makes the owner mean something.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from ..api.sandbox_activity import IActivityStore, LiveSandbox
from ..config.schema import PerUserResources
from .limits import ResourceLimits, parse_size

logger = logging.getLogger(__name__)


class SandboxQuotaExceeded(Exception):
    """Opening another sandbox would put this person over their limit.

    Carries which dimension bound and the numbers behind it, because the only
    useful thing to tell someone is what to close and how much it buys back."""

    def __init__(self, owner: str, dimension: str, used: float, limit: float) -> None:
        super().__init__(
            f"{owner} is at their sandbox limit: {dimension} {used} of {limit} "
            f"already in use by live environments"
        )
        self.owner = owner
        self.dimension = dimension
        self.used = used
        self.limit = limit


class AdmissionGate:
    """Decides whether one more live sandbox may be opened for an item.

    `has_live_sandbox` answers "is this item already holding a slot?" — wired to
    the registry's liveness probe, which is authoritative across pods (#366).
    `owner_of` resolves the debtor; an item with no resolvable owner is NOT
    gated, because charging a limit to nobody can only produce a refusal no
    person can act on.
    """

    def __init__(
        self,
        activity: IActivityStore | None,
        limits: PerUserResources,
        *,
        owner_of: Callable[[str], str | None],
        has_live_sandbox: Callable[[str], Awaitable[bool]],
        window_ms: int,
        now_ms: Callable[[], int],
    ) -> None:
        self._activity = activity
        self._limits = limits
        self._owner_of = owner_of
        self._has_live = has_live_sandbox
        self._window_ms = window_ms
        self._now_ms = now_ms

    @property
    def _configured(self) -> bool:
        lim = self._limits
        return bool(lim.count or lim.cpu or parse_size(lim.memory))

    async def check(self, item_id: str, incoming: ResourceLimits | None = None) -> None:
        """Raise `SandboxQuotaExceeded` if opening a sandbox for `item_id` would
        put its owner over.

        `incoming` is what the NEW sandbox would be allowed to consume (its
        App's ceilings). It matters because the question is not "are you already
        over?" but "does one more of THIS size fit?" — with 3.5 of 4 cores live,
        whether another sandbox is admissible depends entirely on how big it is.

        A no-op when no per-person limit is configured, when there is no shared
        activity store to tally against (single-process), or when the item
        already has a live sandbox."""
        if self._activity is None or not self._configured:
            return
        owner = self._owner_of(item_id)
        if not owner:
            return
        if await self._has_live(item_id):
            return  # already holding its slot — never refuse what is already open
        since = self._now_ms() - self._window_ms
        live = [
            s for s in await self._activity.live_for(owner, since_ms=since) if s.item_id != item_id
        ]
        self._enforce(owner, live, incoming)

    def _enforce(
        self, owner: str, live: list[LiveSandbox], incoming: ResourceLimits | None
    ) -> None:
        lim = self._limits
        add_cpu = incoming.cpu_cores if incoming else 0.0
        add_mem = incoming.memory_bytes if incoming else 0
        checks: tuple[tuple[str, float, float], ...] = (
            ("sandboxes", len(live) + 1, float(lim.count)),
            ("cpu", sum(s.cpu_milli for s in live) / 1000 + add_cpu, lim.cpu),
            (
                "memory",
                float(sum(s.memory_bytes for s in live) + add_mem),
                float(parse_size(lim.memory)),
            ),
        )
        for name, would_be, limit in checks:
            # A limit of 0 means unlimited everywhere in this feature, so it
            # never binds. Exactly reaching the limit is allowed; exceeding it
            # is not — the same boundary the disk quota already uses.
            if limit and would_be > limit:
                logger.info(
                    "quota: refusing new sandbox for %s — %s would reach %s of %s",
                    owner,
                    name,
                    would_be,
                    limit,
                )
                raise SandboxQuotaExceeded(owner, name, would_be, limit)
