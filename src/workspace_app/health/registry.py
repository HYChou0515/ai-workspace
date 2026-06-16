"""CheckRegistry + run_check — registration and uniform execution.

Mirrors the ``kb.parsers`` registry pattern: bundled checks register
in the factory, custom ones come from ``health.checks`` dotted paths.
``run_check`` is the one wrapper every execution path (startup sync /
async, manual re-run) goes through — latency + checked_at stamping and
exception→``error`` conversion live here so checks stay simple.
"""

from __future__ import annotations

import logging
import time

from .protocol import CheckResult, ISanityCheck

logger = logging.getLogger(__name__)


class CheckRegistry:
    def __init__(self) -> None:
        self._checks: list[ISanityCheck] = []

    def register(self, check: ISanityCheck) -> CheckRegistry:
        if any(c.check_id == check.check_id for c in self._checks):
            raise ValueError(f"duplicate check_id {check.check_id!r}")
        self._checks.append(check)
        return self

    def checks(self, *, fast_only: bool = False) -> list[ISanityCheck]:
        if fast_only:
            return [c for c in self._checks if c.fast]
        return list(self._checks)


def run_check(check: ISanityCheck) -> CheckResult:
    """Execute one probe: stamp latency + checked_at; a raising probe
    becomes ``status="error"`` with the cause in detail (wiring
    problem), never an unhandled exception."""
    started = time.perf_counter()
    try:
        result = check.run()
    except Exception as exc:  # noqa: BLE001 — by contract: error points at the wiring
        logger.warning("sanity check %s errored", check.check_id, exc_info=True)
        result = CheckResult(
            check_id=check.check_id,
            status="error",
            detail=f"{type(exc).__name__}: {exc!s}"[:500],
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return CheckResult(
        check_id=result.check_id,
        status=result.status,
        detail=result.detail,
        latency_ms=latency_ms,
        checked_at=int(time.time() * 1000),
    )
