"""CheckRegistry + run_check — registration and uniform execution.

`run_check` is the single wrapper every execution path (startup sync,
startup async, manual re-run) goes through: it stamps latency +
checked_at and converts a raising probe into `status="error"` — checks
themselves stay simple and never need their own try/except.
"""

from __future__ import annotations

import pytest

from workspace_app.health import CheckRegistry, CheckResult, ISanityCheck, run_check


class _Static(ISanityCheck):
    def __init__(self, check_id: str, *, fast: bool = False, result: str = "pass") -> None:
        self.check_id = check_id
        self.description = f"{check_id} probe"
        self.fast = fast
        self._result = result

    def run(self) -> CheckResult:
        return CheckResult(check_id=self.check_id, status=self._result, detail="probed")


class _Boom(ISanityCheck):
    check_id = "boom"
    description = "always raises"

    def run(self) -> CheckResult:
        raise ConnectionError("ollama unreachable at :11434")


def test_register_lists_in_order_and_chains():
    r = CheckRegistry()
    a, b = _Static("a"), _Static("b", fast=True)
    assert r.register(a).register(b) is r
    assert r.checks() == [a, b]
    assert r.checks(fast_only=True) == [b]


def test_duplicate_check_id_raises():
    """check_id is the FE/API key — two checks under one id would
    silently shadow each other in result maps."""
    r = CheckRegistry().register(_Static("dup"))
    with pytest.raises(ValueError, match="dup"):
        r.register(_Static("dup"))


def test_run_check_stamps_latency_and_checked_at():
    res = run_check(_Static("a"))
    assert res.status == "pass"
    assert res.detail == "probed"
    assert res.latency_ms >= 0
    assert res.checked_at > 0  # epoch ms, stamped by the runner


def test_run_check_converts_exceptions_to_error_status():
    """A probe that can't even run is `error` (wiring problem), never an
    unhandled exception — and the cause lands in detail."""
    res = run_check(_Boom())
    assert res.status == "error"
    assert res.check_id == "boom"
    assert "ConnectionError" in res.detail and "11434" in res.detail
    assert res.checked_at > 0
