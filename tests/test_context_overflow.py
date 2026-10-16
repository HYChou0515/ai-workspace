"""#624 P4: a rejection is information, not something to repeat three times.

Today a "prompt too long" 400 is retried with the SAME over-long prompt (the
retry decision never looks at the error), and the hint fed back — "the previous
attempt failed… try again" — is appended to that prompt, making it longer. The
turn then dies showing a raw provider string, and because the failure is itself
persisted the next turn starts from an even longer thread.
"""

from __future__ import annotations

from workspace_app.api.events import RunDone, RunError
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


# ── C1: the overflow branch must have a way out (adversarial review) ──
#
# The retry DECISION (resend / trim / give up) lives on the model call now —
# docs/plan-friendly-errors.md D2, pinned in tests/agent/test_call_retry_model.py.
# These drive the real runner through the real SDK run, with the provider
# rejecting while the request is still too long: the claims are unchanged, only
# the call that is re-sent is now the one that failed, never the whole turn.


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


def _history(sent: list) -> list:
    """What the provider was sent, minus this turn's own message."""
    return sent[:-1]


async def _drive(monkeypatch, llm, ctx, runner=None):
    from workspace_app.api import litellm_runner as lr

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(lr, "LitellmModel", llm)
    runner = runner or lr.LitellmAgentRunner(sleep=no_sleep)
    return [ev async for ev in runner.run("go", ctx)]


async def test_a_length_rejection_shrinks_the_history_and_retries(monkeypatch):
    """The escape hatch #624 exists for. Rejecting once and giving up leaves the
    conversation permanently stuck: the failure is persisted, so the next turn's
    history is LONGER and rejects again — forever. Shrinking is the only exit."""
    from .api._fake_llm import Fail, Say, ScriptedLlm

    def judge(sent):
        if len(_history(sent)) > 4:
            return Fail(_Boom("This model's maximum context length is 32768 tokens."))
        return Say(["ok"])

    llm = ScriptedLlm(judge)
    events = await _drive(monkeypatch, llm, _ctx_with_history(32))

    sizes = [len(_history(s)) for s in llm.inputs]
    assert len(sizes) > 1, "a length rejection must be retried with LESS history"
    assert sizes[1] < sizes[0], "the retry must send a smaller history"
    assert any(isinstance(e, RunDone) for e in events)
    assert not [e for e in events if isinstance(e, RunError)], "shrinking should succeed"


async def test_an_unshrinkable_overflow_ends_the_turn_named_for_what_it_is(monkeypatch):
    """The floor: when there is nothing left to drop the turn ends — named, so
    the FE can say what to do (D5 `context_overflow`), with the provider's own
    text under technical details."""
    from .api._fake_llm import Fail, ScriptedLlm

    llm = ScriptedLlm(
        lambda sent: Fail(_Boom("This model's maximum context length is 8192 tokens."))
    )
    events = await _drive(monkeypatch, llm, _ctx_with_history(1))

    [err] = [e for e in events if isinstance(e, RunError)]
    assert err.kind == "context_overflow"
    assert "8192" in err.message


async def test_the_ceiling_stated_in_the_rejection_is_remembered(monkeypatch):
    """The provider names its limit; that is the cheapest measurement we will
    ever get of an endpoint no registry knows."""
    from workspace_app.api import litellm_runner as lr

    from .api._fake_llm import Fail, ScriptedLlm

    llm = ScriptedLlm(
        lambda sent: Fail(_Boom("This model's maximum context length is 32768 tokens."))
    )
    runner = lr.LitellmAgentRunner()
    await _drive(monkeypatch, llm, _ctx_with_history(2), runner)

    assert runner.learned_limit("m", None) == 32768


def _dumps_thread(ctx):
    ctx.history = [
        {"role": "user", "content": "分析這批資料並寫成報告"},
        *[{"role": "tool", "content": "x" * 6_000} for _ in range(4)],
        {"role": "user", "content": "現在呢?"},
    ]
    return ctx


def _reject_dumps(limit: int):
    from .api._fake_llm import Fail, Say

    def judge(sent):
        # A context rejection is about SIZE. Rejecting on message count would
        # be a premise no provider has, and one no folding policy could ever
        # satisfy — it keeps every message by design.
        if any(len(str(m.get("content", ""))) > 2_000 for m in _history(sent)):
            return Fail(_Boom(f"This model's maximum context length is {limit} tokens."))
        return Say(["ok"])

    return judge


async def test_the_retry_uses_the_configured_policy_when_the_limit_is_stated(monkeypatch):
    """#624: when the rejection states a ceiling we know the budget, so the SAME
    algorithm as the pre-send path decides what to give up; a blind halving is
    only for the case where no ceiling was stated."""
    from .api._fake_llm import ScriptedLlm

    llm = ScriptedLlm(_reject_dumps(5600))
    await _drive(monkeypatch, llm, _dumps_thread(_ctx_with_history(0)))

    assert len(llm.inputs) > 1, "a stated ceiling must drive a retry"
    # The policy folded the dumps rather than blindly dropping the front, so the
    # opening request survived into the retry.
    assert any("分析這批資料" in str(m.get("content")) for m in _history(llm.inputs[-1]))


async def test_the_retry_budget_subtracts_what_is_not_history(monkeypatch):
    """A rejection states the model's TOTAL ceiling, but the request also carries
    the system prompt and every tool schema. Budgeting history against the raw
    stated figure judges an over-long thread to "fit", and the retry falls back
    to blind halving — losing the opening task (§P7)."""
    from .api._fake_llm import ScriptedLlm

    ctx = _dumps_thread(_ctx_with_history(0))
    # Comfortably inside the stated 20,000 on its own, but not once the prompt
    # and tool schemas are counted.
    ctx.context_overhead_tokens = 15_000
    llm = ScriptedLlm(_reject_dumps(20000))
    await _drive(monkeypatch, llm, ctx)

    assert len(llm.inputs) > 1, "a stated ceiling must drive a retry"
    assert any("分析這批資料" in str(m.get("content")) for m in _history(llm.inputs[-1])), (
        "the opening task must survive — losing it is the defect this issue is about"
    )


async def test_folding_counts_as_progress_even_though_it_drops_nothing(monkeypatch):
    """Progress has to be measured in tokens, not in messages: the cheapest stage
    folds bulky tool output and keeps every message, so a "fewer messages?" guard
    would discard a fold that already freed most of the budget."""
    from .api._fake_llm import ScriptedLlm

    llm = ScriptedLlm(_reject_dumps(5600))
    await _drive(monkeypatch, llm, _dumps_thread(_ctx_with_history(0)))

    assert len(llm.inputs) > 1, "a stated ceiling must drive a retry"
    assert len(_history(llm.inputs[-1])) == len(_history(llm.inputs[0])), (
        "folding keeps every message — nothing to drop"
    )
    assert any("分析這批資料" in str(m.get("content")) for m in _history(llm.inputs[-1]))


async def test_a_retry_that_cannot_get_smaller_stops_instead_of_spinning(monkeypatch):
    """The overflow path deliberately bypasses the attempt counter, so its
    termination depends ENTIRELY on every round being smaller — a guarantee
    living inside a function the loop calls. When it lapsed, no test turned red:
    the suite simply never finished. So the caller checks for itself."""
    from workspace_app.api import litellm_runner as lr

    from .api._fake_llm import Fail, ScriptedLlm

    monkeypatch.setattr(lr, "_shrink_history", lambda history, stated, **kw: list(history))
    llm = ScriptedLlm(
        lambda sent: Fail(_Boom("This model's maximum context length is 8192 tokens."))
    )

    events = await _drive(monkeypatch, llm, _ctx_with_history(20))

    assert len(llm.inputs) < 5, "a retry that cannot shrink must stop, not spin"
    [err] = [e for e in events if isinstance(e, RunError)]
    assert err.kind == "context_overflow"


async def test_a_trim_carries_over_to_the_turns_later_calls(monkeypatch):
    """The SDK re-sends its ORIGINAL input on every call of the turn; a cut that
    is forgotten after the call that earned it is relearned, one rejection per
    tool round."""
    from agents import function_tool

    from workspace_app.api import litellm_runner as lr

    from .api._fake_llm import Call, Fail, Say, ScriptedLlm

    @function_tool
    def look() -> str:
        """Look."""
        return "seen"

    monkeypatch.setattr(lr, "build_tools", lambda *a, **k: [look])
    calls = iter([Call("look", {}), Say(["done"])])

    def judge(sent):
        if any(len(str(m.get("content", ""))) > 2_000 for m in sent):
            return Fail(_Boom("This model's maximum context length is 5600 tokens."))
        return next(calls)

    llm = ScriptedLlm(judge)
    events = await _drive(monkeypatch, llm, _dumps_thread(_ctx_with_history(0)))

    assert not [e for e in events if isinstance(e, RunError)]
    assert len(llm.inputs) == 3, "one rejection, then the trimmed input on BOTH calls"
