"""docs/plan-friendly-errors.md D2/D3, through the runner's own composition: a
provider failure is retried as THAT model call, inside the real
`Runner.run_streamed`, never by re-running the turn.

The case that started this is the first test: a 429 after the answer had begun
used to end the turn with `RateLimitError: litellm.RateLimitError: …`."""

from __future__ import annotations

from typing import Any

import httpx
import litellm
import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import litellm_runner as lr
from workspace_app.api.events import MessageDelta, OutputRetracted, RateLimited, RunDone, RunError
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.resources import AgentConfig

from ._fake_llm import Call, Fail, Say, ScriptedLlm


def _resp(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("POST", "http://llm.test/v1")
    )


def _rate_limited(retry_after: str) -> Exception:
    return litellm.RateLimitError(
        "slow down", "openai", "m", response=_resp(429, {"retry-after": retry_after})
    )


async def _no_sleep(_s: float) -> None:
    return None


async def _run(monkeypatch: pytest.MonkeyPatch, llm: ScriptedLlm, **kw: Any) -> list[Any]:
    monkeypatch.setattr(lr, "LitellmModel", llm)
    runner = LitellmAgentRunner(sleep=_no_sleep, **kw)
    ctx = AgentToolContext(investigation_id="i", agent_config=AgentConfig(name="t", model="m"))
    return [ev async for ev in runner.run("go", ctx)]


def _answer(events: list[Any]) -> str:
    """The visible answer as a reader of the stream assembles it — deltas, with
    a retraction withdrawing what the failed call had shown."""
    text = ""
    for ev in events:
        if isinstance(ev, MessageDelta) and not ev.reasoning:
            text += ev.text
        elif isinstance(ev, OutputRetracted):
            text = text[: len(text) - ev.text_chars]
    return text


async def test_a_rate_limit_mid_answer_is_waited_out_and_the_answer_continues(monkeypatch):
    llm = ScriptedLlm([Fail(_rate_limited("3"), deltas=["half an "]), Say(["the whole answer"])])

    events = await _run(monkeypatch, llm)

    assert not [e for e in events if isinstance(e, RunError)]
    assert [e.seconds for e in events if isinstance(e, RateLimited)] == [3.0]
    assert _answer(events) == "the whole answer"
    assert isinstance(events[-1], RunDone)
    assert len(llm.inputs) == 2 and llm.inputs[0] == llm.inputs[1], "the SAME call, re-sent"


async def test_a_tool_that_already_ran_is_not_run_again(monkeypatch):
    """The point of retrying the CALL: the turn loop restarted the whole run,
    which would have executed the tool a second time — the reason it refused
    once anything had streamed, and so the reason this failure ended turns."""
    from agents import function_tool

    runs: list[str] = []

    @function_tool
    def note(text: str) -> str:
        """Record a note."""
        runs.append(text)
        return "noted"

    monkeypatch.setattr(lr, "build_tools", lambda *a, **k: [note])
    unavailable = litellm.ServiceUnavailableError("down", "openai", "m", response=_resp(503))
    llm = ScriptedLlm(
        [
            Call("note", {"text": "once"}),
            Fail(unavailable, deltas=["I noted it and "]),
            Say(["I noted it."]),
        ]
    )

    events = await _run(monkeypatch, llm)

    assert runs == ["once"]
    assert not [e for e in events if isinstance(e, RunError)]
    assert _answer(events) == "I noted it."
    assert llm.inputs[1] == llm.inputs[2], "the second call re-sent as it was"


async def test_a_tool_can_put_a_hold_on_the_turns_own_status_line(monkeypatch):
    """P4: a sub-agent's hold is the TURN waiting — the delegating tool hands it
    to `ctx.on_turn_status`, and the runner puts it on this turn's stream."""
    from agents import function_tool

    @function_tool
    async def delegate(ctx: RunContextWrapper[AgentToolContext]) -> str:
        """Delegate."""
        assert ctx.context.on_turn_status is not None
        ctx.context.on_turn_status(RateLimited(seconds=7))
        return "done"

    monkeypatch.setattr(lr, "build_tools", lambda *a, **k: [delegate])
    llm = ScriptedLlm([Call("delegate", {}), Say(["ok"])])

    events = await _run(monkeypatch, llm)

    assert [e.seconds for e in events if isinstance(e, RateLimited)] == [7.0]


async def test_a_delegated_failure_reaches_the_tool_card_named_and_the_model_raw(monkeypatch):
    """P4 / D10: the card says what happened to the sub-agent; the model reads the
    raw text, which it can act on. The failure is recorded against the CALL, so
    whatever the tool does to its result on the way out cannot unpair them."""
    from agents import function_tool

    from workspace_app.agent.tools import _delegation
    from workspace_app.api.events import ToolEnd

    @function_tool
    async def delegate(ctx: RunContextWrapper[AgentToolContext]) -> str:
        """Delegate."""
        _delegation(ctx).failed(
            RunError(message="RateLimitError: slow", kind="rate_limited", params={"waited_s": 120})
        )
        return "[sub-agent ran on m]\n\nsub-agent 'x' failed: RateLimitError: slow"

    monkeypatch.setattr(lr, "build_tools", lambda *a, **k: [delegate])
    llm = ScriptedLlm([Call("delegate", {}), Say(["ok"])])

    events = await _run(monkeypatch, llm)

    [end] = [e for e in events if isinstance(e, ToolEnd)]
    assert end.failure == {
        "kind": "rate_limited",
        "params": {"waited_s": 120},
        "raw": "RateLimitError: slow",
    }
    assert "RateLimitError: slow" in end.output, "the model still reads the raw text"


def _unavailable() -> Exception:
    return litellm.ServiceUnavailableError("down", "openai", "m", response=_resp(503))


async def test_a_call_that_never_recovers_ends_the_turn_named_and_counted(monkeypatch):
    """No turn-level retry on top: the call's own retries are the whole budget,
    and the ending says what failed and how often it was tried (D5)."""
    llm = ScriptedLlm([Fail(_unavailable()) for _ in range(9)])

    events = await _run(monkeypatch, llm, max_retries=2)

    [err] = [e for e in events if isinstance(e, RunError)]
    assert (err.kind, err.params) == ("provider_error", {"attempts": 3})
    assert "ServiceUnavailableError" in err.message, "the raw text is the technical detail"
    assert len(llm.inputs) == 3, "three sends of ONE call — never the turn again"


async def test_a_rate_limit_that_never_clears_says_how_long_it_waited(monkeypatch):
    llm = ScriptedLlm([Fail(_rate_limited("50")) for _ in range(3)])

    events = await _run(monkeypatch, llm, rate_limit_budget_s=60.0)

    [err] = [e for e in events if isinstance(e, RunError)]
    assert (err.kind, err.params) == ("rate_limited", {"waited_s": 50})
    assert len(llm.inputs) == 2


async def test_a_request_that_is_wrong_ends_the_turn_at_once(monkeypatch):
    bad = litellm.BadRequestError("unsupported param", "m", "openai", response=_resp(400))
    llm = ScriptedLlm([Fail(bad), Say(["never"])])

    events = await _run(monkeypatch, llm)

    [err] = [e for e in events if isinstance(e, RunError)]
    assert (err.kind, err.params) == ("bad_request", None)
    assert len(llm.inputs) == 1


async def test_the_provider_client_does_not_retry_behind_our_back(monkeypatch):
    """Found recording the demo: a 429 with `Retry-After: 4` never reached this
    layer. litellm builds its OpenAI client with `max_retries=2`, and that client
    waits out a short Retry-After and re-sends on its own — so the turn sat on
    "waiting for the model" with no hold, no count and no budget charged. The
    wrapper is the one retry layer only if every call it makes says so; this
    goes through the real `LitellmModel` to the `acompletion` it calls."""
    seen: list[dict[str, Any]] = []

    async def acompletion(**kwargs: Any) -> Any:
        seen.append(kwargs)
        raise litellm.BadRequestError("stop here", "m", "openai", response=_resp(400))

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    runner = LitellmAgentRunner(sleep=_no_sleep)
    ctx = AgentToolContext(investigation_id="i", agent_config=AgentConfig(name="t", model="m"))

    events = [ev async for ev in runner.run("go", ctx)]

    assert [e.kind for e in events if isinstance(e, RunError)] == ["bad_request"]
    assert [(k["max_retries"], k["num_retries"]) for k in seen] == [(0, 0)]
