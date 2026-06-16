"""HealthService — cached results + the two execution paths (startup
fast-sync, full async round) with a one-round-at-a-time guard.

Q2 contract: boot runs the FAST set synchronously (<1 min,
connectivity-grade), then the FULL set asynchronously; the FE can
re-run all or one check on demand. Results are cached with timestamps;
`running` tells the FE a round is in flight.
"""

from __future__ import annotations

import asyncio
import threading

from workspace_app.health import CheckRegistry, CheckResult, ISanityCheck
from workspace_app.health.service import HealthService


class _Static(ISanityCheck):
    def __init__(self, check_id: str, *, fast: bool = False, status: str = "pass") -> None:
        self.check_id = check_id
        self.description = f"{check_id} probe"
        self.fast = fast
        self._status = status
        self.runs = 0

    def run(self) -> CheckResult:
        self.runs += 1
        return CheckResult(check_id=self.check_id, status=self._status)


def _registry() -> tuple[CheckRegistry, _Static, _Static]:
    fast = _Static("conn", fast=True)
    slow = _Static("capability", status="fail")
    reg = CheckRegistry().register(fast).register(slow)
    return reg, fast, slow


def test_results_start_empty_and_not_running():
    reg, _, _ = _registry()
    svc = HealthService(reg)
    assert svc.results() == []
    assert svc.running is False


def test_run_fast_sync_populates_only_fast_checks():
    reg, fast, slow = _registry()
    svc = HealthService(reg)
    svc.run_fast_sync()
    assert [r.check_id for r in svc.results()] == ["conn"]
    assert fast.runs == 1 and slow.runs == 0
    # Stamped by the runner.
    assert svc.results()[0].checked_at > 0


def test_run_round_runs_all_and_caches_latest():
    reg, fast, slow = _registry()
    svc = HealthService(reg)
    asyncio.run(svc.run_round())
    by_id = {r.check_id: r for r in svc.results()}
    assert by_id["conn"].status == "pass"
    assert by_id["capability"].status == "fail"
    # A second round replaces, not appends.
    asyncio.run(svc.run_round())
    assert len(svc.results()) == 2
    assert fast.runs == 2 and slow.runs == 2


def test_run_round_single_check_only():
    reg, fast, slow = _registry()
    svc = HealthService(reg)
    asyncio.run(svc.run_round(only="capability"))
    assert [r.check_id for r in svc.results()] == ["capability"]
    assert fast.runs == 0 and slow.runs == 1


def test_concurrent_round_is_refused():
    """One round at a time — the probes hammer the same local model;
    a second trigger while running returns False and runs nothing."""

    class _Blocking(ISanityCheck):
        check_id = "blocking"
        description = "waits"
        fast = False

        def __init__(self) -> None:
            self.release = threading.Event()
            self.entered = threading.Event()

        def run(self) -> CheckResult:
            self.entered.set()
            assert self.release.wait(timeout=5)
            return CheckResult(check_id=self.check_id, status="pass")

    blocking = _Blocking()
    reg = CheckRegistry().register(blocking)
    svc = HealthService(reg)

    async def scenario() -> tuple[bool, bool]:
        first = asyncio.create_task(svc.run_round())
        await asyncio.to_thread(blocking.entered.wait, 5)
        assert svc.running is True
        second = await svc.run_round()  # refused immediately
        blocking.release.set()
        started = await first
        return started, second

    started_first, started_second = asyncio.run(scenario())
    assert started_first is True
    assert started_second is False
    assert svc.running is False


def test_on_result_hook_receives_each_result():
    reg, _, _ = _registry()
    seen: list[str] = []
    svc = HealthService(reg, on_result=lambda r: seen.append(r.check_id))
    asyncio.run(svc.run_round())
    assert seen == ["conn", "capability"]


def test_on_result_hook_failure_does_not_break_the_round():
    reg, _, _ = _registry()

    def boom(_r: CheckResult) -> None:
        raise RuntimeError("persistence down")

    svc = HealthService(reg, on_result=boom)
    asyncio.run(svc.run_round())
    assert len(svc.results()) == 2  # round still completed + cached
