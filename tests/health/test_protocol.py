"""ISanityCheck + CheckResult — the #51 check framework contract.

Three audiences rely on this contract:
  - bundled check authors (the seven capability probes, P2),
  - operators adding custom checks via `health.checks` dotted paths,
  - the runner (`run_check`) that wraps every check uniformly.

Statuses: `pass` (probe ran, capability confirmed), `fail` (probe ran,
the MODEL can't do the task — the qwen3:14b lesson), `skip` (feature
not configured, e.g. `vlm_llm: null`), `error` (probe itself couldn't
run — connectivity, exception). fail vs error matters: fail points at
the model, error points at the wiring.
"""

from __future__ import annotations

from abc import ABC

import pytest

from workspace_app.health import CheckResult, ISanityCheck


def test_isanitycheck_is_abc_requiring_run():
    assert ABC in ISanityCheck.__mro__

    class Missing(ISanityCheck):
        check_id = "x"
        description = "x"

    with pytest.raises(TypeError, match="abstract"):
        Missing()  # type: ignore[abstract]


def test_a_complete_check_constructs_with_metadata():
    class Ok(ISanityCheck):
        check_id = "demo"
        description = "demo probe"

        def run(self) -> CheckResult:
            return CheckResult(check_id=self.check_id, status="pass")

    c = Ok()
    assert c.check_id == "demo"
    # Checks default to SLOW (capability probes); fast connectivity
    # checks opt in — startup runs only the fast set synchronously.
    assert c.fast is False
    assert c.run().status == "pass"


def test_fast_is_an_overridable_class_attribute():
    class Quick(ISanityCheck):
        check_id = "conn"
        description = "connectivity"
        fast = True

        def run(self) -> CheckResult:
            return CheckResult(check_id=self.check_id, status="pass")

    assert Quick().fast is True


def test_checkresult_rejects_unknown_status():
    """The four statuses are the FE contract — a typo'd status must die
    at construction, not render as an unknown chip."""
    with pytest.raises(ValueError, match="status"):
        CheckResult(check_id="x", status="degraded")


def test_checkresult_defaults():
    r = CheckResult(check_id="x", status="skip")
    assert r.detail == ""
    assert r.latency_ms == 0
    assert r.checked_at == 0
