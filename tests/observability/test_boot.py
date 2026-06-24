"""`observability.boot` — `boot_step`, the startup narration context manager.

Each blocking boot step is wrapped so the operator sees `→ step …` on enter and
`✓ step (1.2s)` on exit. A hang leaves the unmatched `→` line as the last thing
printed, naming the culprit (#208: pod hung silently after the config dump).
"""

from __future__ import annotations

import io

import pytest


def test_enter_prints_arrow_line():
    from workspace_app.observability.boot import boot_step

    out = io.StringIO()
    with boot_step("get_spec", stream=out):
        pass
    assert "→ get_spec" in out.getvalue()


def _fake_clock(*values: float):
    """A clock returning the given values in sequence (start, end, …)."""
    it = iter(values)
    return lambda: next(it)


def test_success_prints_check_line_with_elapsed():
    from workspace_app.observability.boot import boot_step

    out = io.StringIO()
    with boot_step("get_spec", stream=out, clock=_fake_clock(10.0, 11.5)):
        pass
    assert "✓ get_spec (1.5s)" in out.getvalue()


def test_exception_prints_cross_line_and_propagates():
    from workspace_app.observability.boot import boot_step

    out = io.StringIO()
    with (
        pytest.raises(RuntimeError, match="boom"),
        boot_step("apply spec to backend", stream=out, clock=_fake_clock(10.0, 20.0)),
    ):
        raise RuntimeError("boom")
    text = out.getvalue()
    assert "✗ apply spec to backend (failed after 10.0s): RuntimeError" in text
    assert "✓" not in text  # failure must NOT also print a success line


def test_defaults_to_live_stdout_so_capture_works(capsys):
    # No stream= → must resolve sys.stdout at CALL time (not bound at import),
    # otherwise pytest's capture / a reassigned stdout would never see it. This
    # is what lets the main()/lifespan wiring narrate to the real console.
    from workspace_app.observability.boot import boot_step

    with boot_step("init embedder"):
        pass
    captured = capsys.readouterr()
    assert "→ init embedder" in captured.out
    assert "✓ init embedder" in captured.out
