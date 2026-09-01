"""#748 — what a reply's record is allowed to claim.

The turn's telemetry has two consumers with opposite requirements: the live
line a person reads (where an approximation beats a blank) and the record we
keep (where an approximation is a number nobody can tell apart from a
measurement). They shared one field, so the record inherited the guess.

These cover the record. The display fields are deliberately untouched — their
own tests still pin the fallback that keeps the line from reading `↑0 ↓0`.

`_TurnReducer` is underscore-named but it IS the seam: it is what turns a
turn's events into the rows that get persisted, so this is a test of what we
store, not of how the engine is wired.
"""

from __future__ import annotations

from workspace_app.api.events import AgentMetrics, MessageDelta
from workspace_app.api.turns import _TurnReducer


def _answered(*events) -> _TurnReducer:
    r = _TurnReducer()
    r.add(MessageDelta(text="hi"))
    for ev in events:
        r.add(ev)
    return r


def test_the_record_keeps_no_number_when_the_provider_reported_none():
    """The live fields still carry something to show; the record must not
    borrow it. `None` is the only honest answer to "how many tokens?" when the
    provider never said — and it is the one value a later reader cannot
    mistake for a measurement."""
    r = _answered(
        AgentMetrics(phase="final", prompt_tokens=120, completion_tokens=50, elapsed_ms=3400)
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert m.prompt_tokens is None
    assert m.completion_tokens is None
    assert m.elapsed_ms == 3400  # the wall clock was measured, so it is kept


def test_the_record_keeps_the_providers_own_numbers_when_it_did_report():
    r = _answered(
        AgentMetrics(
            phase="final",
            prompt_tokens=120,
            completion_tokens=50,
            elapsed_ms=3400,
            measured_prompt_tokens=8412,
            measured_completion_tokens=356,
        )
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert (m.prompt_tokens, m.completion_tokens) == (8412, 356)


def test_a_mid_stream_tick_does_not_overwrite_the_record():
    """`down` ticks carry approximations only. The reducer used to write the
    record on EVERY metrics event and was correct only because `final` happens
    to arrive last — a coincidence, not a design. Now that the record's fields
    are absent on a tick, relying on that ordering would blank a good record
    every 0.2 seconds."""
    r = _answered(
        AgentMetrics(
            phase="final",
            elapsed_ms=3400,
            measured_prompt_tokens=8412,
            measured_completion_tokens=356,
        ),
        AgentMetrics(phase="down", prompt_tokens=120, completion_tokens=50, elapsed_ms=3600),
    )

    m = r.produced[-1].metrics
    assert m is not None
    assert (m.prompt_tokens, m.completion_tokens) == (8412, 356)


def test_a_reported_zero_is_not_a_measurement():
    """Local Ollama routinely streams `usage` with zeros. Zero tokens is not a
    thing that happens — a reply exists, so something was read and something was
    written. `0` therefore means "this provider did not count", and recording it
    as a measured zero would poison every average computed over it."""
    from workspace_app.api.litellm_runner import _measured_tokens

    assert _measured_tokens(None) == (None, None)
    assert _measured_tokens((0, 0)) == (None, None)
    assert _measured_tokens((8412, 356)) == (8412, 356)
    assert _measured_tokens((8412, 0)) == (8412, None)  # per field, not all-or-nothing
