"""HealthService — cached check results + the two execution paths.

Q2 contract (docs/plan-sanity-checks.md): boot runs the FAST set
synchronously (connectivity-grade, <1 min), then the FULL set
asynchronously; the FE re-runs all or one check on demand. One round
at a time — the probes hammer the same local model, so a second
trigger while one is in flight is refused (the FE shows `running`).

``on_result`` is the persistence hook (the app wires it to a specstar
``CheckRun`` create) — a failing hook never breaks the round.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

from .protocol import CheckResult
from .registry import CheckRegistry, run_check

logger = logging.getLogger(__name__)


class HealthService:
    def __init__(
        self,
        registry: CheckRegistry,
        *,
        on_result: Callable[[CheckResult], None] | None = None,
    ) -> None:
        self._registry = registry
        self._on_result = on_result
        self._results: dict[str, CheckResult] = {}
        self._lock = threading.Lock()
        self._running = False

    @property
    def registry(self) -> CheckRegistry:
        return self._registry

    @property
    def running(self) -> bool:
        return self._running

    def results(self) -> list[CheckResult]:
        """Latest result per check, in registry order."""
        return [
            self._results[c.check_id]
            for c in self._registry.checks()
            if c.check_id in self._results
        ]

    def run_fast_sync(self) -> None:
        """The startup-blocking part: connectivity-grade checks only."""
        logger.info("health: running fast (startup) sanity checks")
        for check in self._registry.checks(fast_only=True):
            self._record(run_check(check))

    async def run_round(self, only: str | None = None) -> bool:
        """Run all checks (or one, by check_id) off the event loop.
        Returns False — having run nothing — when a round is already in
        flight."""
        with self._lock:
            if self._running:
                logger.info("health: run_round refused — a round is already in flight")
                return False
            self._running = True
        try:
            checks = [c for c in self._registry.checks() if only is None or c.check_id == only]
            logger.info("health: running sanity check round (only=%s)", only)
            for check in checks:
                # Sequential by design: capability probes target the same
                # local model; parallelism would just contend.
                self._record(await asyncio.to_thread(run_check, check))
            logger.info("health: sanity check round complete (only=%s)", only)
            return True
        finally:
            self._running = False

    def _record(self, result: CheckResult) -> None:
        self._results[result.check_id] = result
        if self._on_result is not None:
            try:
                self._on_result(result)
            except Exception:  # noqa: BLE001 — persistence must not break the round
                logger.warning("check result hook failed for %s", result.check_id, exc_info=True)
