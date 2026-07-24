"""#624 P4: a rejection is information, not something to repeat three times.

Today a "prompt too long" 400 is retried with the SAME over-long prompt (the
retry decision never looks at the error), and the hint fed back — "the previous
attempt failed… try again" — is appended to that prompt, making it longer. The
turn then dies showing a raw provider string, and because the failure is itself
persisted the next turn starts from an even longer thread.
"""

from __future__ import annotations

from workspace_app.api.events import MessageDelta, RunDone, RunError
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


# ── C1: the overflow branch must have a way out (adversarial review) ──


class _Boom(Exception):
    pass


def _ctx_with_history(n: int):
    from workspace_app.agent.context import AgentToolContext
    from workspace_app.resources import AgentConfig

    return AgentToolContext(
        investigation_id="i",
        agent_config=AgentConfig(name="t", model="m", system_prompt="s"),
        history=[{"role": "user", "content": f"m{i}"} for i in range(n)],
    )


async def _drive(runner, ctx):
    return [ev async for ev in runner.run("go", ctx)]


async def test_a_length_rejection_shrinks_the_history_and_retries():
    """The escape hatch #624 exists for. Rejecting once and giving up leaves the
    conversation permanently stuck: the failure is persisted, so the next turn's
    history is LONGER and rejects again — forever. Shrinking is the only exit."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    seen_sizes: list[int] = []

    class _Runner(LitellmAgentRunner):
        async def _run_once(self, prompt, ctx, feedback):  # type: ignore[override]
            seen_sizes.append(len(ctx.history))
            if len(ctx.history) > 4:
                raise _Boom("This model's maximum context length is 32768 tokens.")
            yield MessageDelta(text="ok")

    events = await _drive(_Runner(), _ctx_with_history(32))

    assert len(seen_sizes) > 1, "a length rejection must be retried with LESS history"
    assert seen_sizes[1] < seen_sizes[0], "the retry must send a smaller history"
    assert any(isinstance(e, RunDone) for e in events)
    assert not [e for e in events if isinstance(e, RunError)], "shrinking should succeed"


async def test_an_unshrinkable_overflow_fails_loud_and_actionably():
    """The floor: when there is nothing left to drop, say so in words the user
    can act on — not a raw provider string they cannot read."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    class _Runner(LitellmAgentRunner):
        async def _run_once(self, prompt, ctx, feedback):  # type: ignore[override]
            raise _Boom("This model's maximum context length is 8192 tokens.")
            yield  # pragma: no cover — unreachable, keeps this an async generator

    events = await _drive(_Runner(), _ctx_with_history(1))

    errs = [e for e in events if isinstance(e, RunError)]
    assert errs, "an unshrinkable overflow must surface an error"
    assert "新對話" in errs[0].message or "太長" in errs[0].message


async def test_the_ceiling_stated_in_the_rejection_is_remembered():
    """The provider names its limit; that is the cheapest measurement we will
    ever get of an endpoint no registry knows."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    class _Runner(LitellmAgentRunner):
        async def _run_once(self, prompt, ctx, feedback):  # type: ignore[override]
            raise _Boom("This model's maximum context length is 32768 tokens.")
            yield  # pragma: no cover

    runner = _Runner()
    await _drive(runner, _ctx_with_history(2))

    assert runner.learned_limit("m", None) == 32768


async def test_the_retry_uses_the_configured_policy_when_the_limit_is_stated():
    """#624: the 400-retry path had its own hardcoded "drop the older half" —
    the same product decision the reduction algorithm owns, answered twice and
    differently. When the rejection states a ceiling we know the budget, so the
    SAME algorithm decides what to give up; a blind halving is only for the case
    where no ceiling was stated and there is nothing to aim at."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    seen: list[list[dict]] = []

    class _Runner(LitellmAgentRunner):
        async def _run_once(self, prompt, ctx, feedback):  # type: ignore[override]
            seen.append(list(ctx.history))
            if len(ctx.history) > 3:
                raise _Boom("This model's maximum context length is 40 tokens.")
            yield MessageDelta(text="ok")

    ctx = _ctx_with_history(0)
    ctx.history = [
        {"role": "user", "content": "分析這批資料並寫成報告"},
        *[{"role": "tool", "content": "x" * 6_000} for _ in range(4)],
        {"role": "user", "content": "現在呢?"},
    ]

    await _drive(_Runner(), ctx)

    assert len(seen) > 1, "a stated ceiling must drive a retry"
    # The policy folded the dumps rather than blindly dropping the front, so the
    # opening request survived into the retry.
    assert any("分析這批資料" in m["content"] for m in seen[-1])
