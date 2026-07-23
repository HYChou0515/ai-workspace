"""#624 P4: a rejection is information, not something to repeat three times.

Today a "prompt too long" 400 is retried with the SAME over-long prompt (the
retry decision never looks at the error), and the hint fed back — "the previous
attempt failed… try again" — is appended to that prompt, making it longer. The
turn then dies showing a raw provider string, and because the failure is itself
persisted the next turn starts from an even longer thread.
"""

from __future__ import annotations

from workspace_app.context_budget import (
    halve_history,
    is_context_overflow,
    parse_limit_from_error,
)

# ── telling the two kinds of 400 apart ──────────────────────────────


def test_a_length_rejection_is_recognised():
    """vLLM / OpenAI-compatible wording."""
    assert is_context_overflow(
        "This model's maximum context length is 32768 tokens. However, you requested "
        "41234 tokens (39234 in the messages, 2000 in the completion)."
    )
    assert is_context_overflow("invalid_request_error: prompt is too long: 9000 tokens > 8192")


def test_an_ordinary_bad_request_is_not_a_length_problem():
    """Halving the history cannot fix a malformed parameter — and retrying it
    at all is the waste this phase removes."""
    assert not is_context_overflow("invalid_request_error: unknown parameter 'foo'")
    assert not is_context_overflow("connection refused")
    assert not is_context_overflow("")


# ── learning the ceiling from the rejection ─────────────────────────


def test_the_limit_is_read_out_of_the_message():
    """The endpoint states its ceiling outright — take it rather than guess."""
    got = parse_limit_from_error(
        "This model's maximum context length is 32768 tokens. However, you requested 41234 tokens."
    )
    assert got == 32768


def test_the_limit_is_read_from_the_shorter_wording():
    assert parse_limit_from_error("prompt is too long: 9000 tokens > 8192 maximum") == 8192


def test_an_unparseable_message_yields_no_limit():
    """Never invent one — an invented ceiling would govern every later turn."""
    assert parse_limit_from_error("invalid_request_error: something went wrong") is None


# ── the productive retry ────────────────────────────────────────────


def test_halving_keeps_the_newest_half():
    """The turn's own context is the last thing worth losing."""
    assert halve_history([1, 2, 3, 4, 5, 6]) == [4, 5, 6]


def test_halving_converges_to_a_single_message():
    kept = list(range(40))
    for _ in range(10):
        kept = halve_history(kept)
    assert kept == [39]


def test_halving_a_single_message_cannot_shrink_further():
    """The floor: one message that alone exceeds the window is a fail-loud
    case (say which message), never an infinite loop."""
    assert halve_history([1]) == [1]
    assert halve_history([]) == []


# ── the retry decision (the "3 identical attempts" defect) ───────────


def test_a_length_rejection_is_not_retried_blindly():
    """#624: `_should_retry` looked only at "did anything stream" and "how many
    attempts" — never at WHAT failed. A deterministic length rejection was
    therefore re-sent unchanged up to three times. It must not be."""
    from workspace_app.api.litellm_runner import _should_retry

    assert not _should_retry(
        progress_made=False,
        attempt=1,
        max_retries=2,
        error_text="This model's maximum context length is 32768 tokens.",
    )


def test_an_ordinary_bad_request_is_not_retried_either():
    """A malformed parameter fails identically every time; three attempts is
    three times the latency for the same error."""
    from workspace_app.api.litellm_runner import _should_retry

    assert not _should_retry(
        progress_made=False,
        attempt=1,
        max_retries=2,
        error_text="invalid_request_error: unknown parameter 'foo'",
    )


def test_the_small_model_json_retry_still_works():
    """The retry exists for the #76 case — a small model botching tool-call
    JSON, where a hint genuinely helps. That must survive."""
    from workspace_app.api.litellm_runner import _should_retry

    assert _should_retry(
        progress_made=False,
        attempt=1,
        max_retries=2,
        error_text="Expecting value: line 1 column 1 (char 0)",
    )


def test_progress_still_beats_everything():
    from workspace_app.api.litellm_runner import _should_retry

    assert not _should_retry(
        progress_made=True, attempt=1, max_retries=2, error_text="some transient blip"
    )


def test_the_length_hint_tells_the_model_something_actionable():
    """The catch-all hint ("the previous attempt failed… try again") is appended
    to a prompt that is ALREADY too long, so it makes the next attempt worse."""
    from workspace_app.api.litellm_runner import diagnose_error

    hint = diagnose_error(RuntimeError("This model's maximum context length is 32768 tokens."))
    assert "try again" not in hint.lower()
    assert "shorter" in hint.lower() or "too long" in hint.lower()
