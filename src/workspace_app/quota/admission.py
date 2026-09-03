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
from typing import TYPE_CHECKING

from ..config.schema import PerUserResources
from .limits import ResourceLimits, parse_size

if TYPE_CHECKING:  # pragma: no cover — types only
    # Imported for typing ONLY. `quota` sits below `api` in the import graph and
    # must stay there: a runtime import here closes a cycle (`api.app` needs the
    # gate, this module would need `api`), and at runtime the gate only ever
    # calls `live_for` on whatever it was handed.
    from ..api.sandbox_activity import IActivityStore, LiveSandbox

logger = logging.getLogger(__name__)


class SandboxQuotaExceeded(Exception):
    """Opening another sandbox would put this person over their limit.

    Carries which dimension bound and the numbers behind it, because the only
    useful thing to tell someone is what to close and how much it buys back."""

    def __init__(
        self,
        owner: str,
        dimension: str,
        used: float,
        limit: float,
        holding: list[LiveSandbox] | None = None,
    ) -> None:
        super().__init__(
            f"{owner} is at their sandbox limit: {dimension} {used} of {limit} "
            f"already in use by live environments"
        )
        self.owner = owner
        self.dimension = dimension
        self.used = used
        self.limit = limit
        #: WHICH environments are holding it. The docstring above has always
        #: promised "what to close and how much it buys back", and the numbers
        #: alone answer only the second half — leaving the person to work out who
        #: is holding their quota, on a page they have to know exists. Ids and
        #: figures only; a title is a store lookup and does not belong in an
        #: exception constructor.
        self.holding: list[LiveSandbox] = list(holding or [])


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
        limits_for: Callable[[str], Awaitable[PerUserResources]],
        *,
        owner_of: Callable[[str], str | None],
        has_live_sandbox: Callable[[str], Awaitable[bool]],
        cost_of: Callable[[str], Awaitable[ResourceLimits]] | None,
        window_ms: int,
        now_ms: Callable[[], int],
    ) -> None:
        self._activity = activity
        # What a NEW sandbox for an item would really cost — asked here rather
        # than passed in by each caller. The one production call site passed
        # nothing, so the incoming sandbox was weighed as free and `cpu` could
        # only refuse someone who was ALREADY over.
        #
        # REQUIRED, with no default, even though `None` is a legal value. An
        # optional one still reads as "a rule you cannot forget" while letting
        # the next `AdmissionGate(...)` silently reinstate exactly that defect;
        # passing `None` on purpose is a decision, forgetting is an accident,
        # and only one of them should compile.
        self._cost_of = cost_of
        # Resolved per check, not captured at construction: raising someone's
        # allowance has to take effect on their next turn, not at the next
        # restart. It is one point read on a path already about to query the
        # ledger.
        self._limits_for = limits_for
        self._owner_of = owner_of
        self._has_live = has_live_sandbox
        self._window_ms = window_ms
        self._now_ms = now_ms

    @staticmethod
    def _configured(lim: PerUserResources) -> bool:
        return bool(lim.count or lim.cpu or parse_size(lim.memory))

    async def check(self, item_id: str, incoming: ResourceLimits | None = None) -> None:
        """Raise `SandboxQuotaExceeded` if opening a sandbox for `item_id` would
        put its owner over.

        `incoming` is what the NEW sandbox would be allowed to consume — what
        the BACKEND will really cap it at, which is its App's ceilings when the
        App states them and the backend's own when it does not. It matters
        because the question is not "are you already over?" but "does one more
        of THIS size fit?" — with 3.5 of 4 cores live, whether another sandbox is
        admissible depends entirely on how big it is. Defaulted from `cost_of`
        rather than passed by each caller: the one production call site passed
        nothing, so the incoming sandbox was weighed as free.

        A no-op when no per-person limit is configured, when there is no shared
        activity store to tally against (single-process), or when the item
        already has a live sandbox."""
        if self._activity is None:
            return
        owner = self._owner_of(item_id)
        if not owner:
            return
        limits = await self._limits_for(owner)
        if not self._configured(limits):
            return
        if await self._has_live(item_id):
            return  # already holding its slot — never refuse what is already open
        if incoming is None and self._cost_of is not None:
            incoming = await self._cost_of(item_id)
        since = self._now_ms() - self._window_ms
        live = [
            s for s in await self._activity.live_for(owner, since_ms=since) if s.item_id != item_id
        ]
        self._enforce(owner, live, incoming, limits)

    def _enforce(
        self,
        owner: str,
        live: list[LiveSandbox],
        incoming: ResourceLimits | None,
        lim: PerUserResources,
    ) -> None:
        # A dimension contributes nothing only when NOBODY states it — neither
        # the App nor the backend. `incoming` is what the backend will really
        # apply (`Sandbox.effective_limits`), so a sandbox whose App declares
        # nothing still carries the ceiling its cgroup will get. Reading the
        # App's declaration alone is what let cpu/memory sum to zero and never
        # bind (`quota.limits` module docstring).
        add_cpu = (incoming.cpu_cores or 0.0) if incoming else 0.0
        add_mem = (incoming.memory_bytes or 0) if incoming else 0
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
                raise SandboxQuotaExceeded(owner, name, would_be, limit, holding=live)
