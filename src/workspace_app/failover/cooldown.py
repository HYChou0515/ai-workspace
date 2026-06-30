"""CooldownRegistry — "this (model, endpoint) is hot, skip it for a bit".

A single process-global instance is shared across every failover role: because
KB and the app hit the same models, "the app found X overloaded" should make KB
skip X too. Keyed by an opaque hashable identity (we use ``(model, endpoint)``).

The clock is injected (``time.monotonic`` in production) so cooldown expiry is
unit-testable without real sleeping. Recovery is time-expiry half-open: once the
deadline passes the key is simply no longer cooling, so the next request
re-probes it; success or failure then re-decides naturally.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable


class CooldownRegistry:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._until: dict[Hashable, float] = {}

    def now(self) -> float:
        """The current reading of the injected clock — so a failover loop can
        measure a total-time deadline against the SAME clock cooldown uses."""
        return self._clock()

    def is_cooling(self, key: Hashable) -> bool:
        """True while ``key`` is parked — i.e. now is strictly before its
        cooldown deadline. An unmarked key is never cooling."""
        until = self._until.get(key)
        return until is not None and self._clock() < until

    def remaining(self, keys: Iterable[Hashable]) -> float:
        """Seconds until the SOONEST of ``keys`` becomes available again — the
        "wake alarm" for a cooldown-aware re-sweep. ``0.0`` when at least one key
        is available *now* (unmarked or expired) or ``keys`` is empty: there is
        nothing to wait for. When every key is cooling, the minimum remaining
        time across them (re-sweeping any sooner would just skip them all)."""
        now = self._clock()
        soonest: float | None = None
        for key in keys:
            until = self._until.get(key)
            if until is None or until <= now:
                return 0.0  # this one is free now — no point waiting
            rem = until - now
            if soonest is None or rem < soonest:
                soonest = rem
        return soonest if soonest is not None else 0.0

    def mark(self, key: Hashable, seconds: float) -> None:
        """Park ``key`` for ``seconds`` from now (overwriting any prior deadline)."""
        self._until[key] = self._clock() + seconds
