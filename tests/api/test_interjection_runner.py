"""docs/plan-interjection.md P3 through the REAL agents-SDK loop.

The runner asks for its author's messages before every model call
(`RunConfig.call_model_input_filter`); one that arrived is read there, and the
turn continues as a NEW segment with the step budget counted afresh (Q4). Only
the model is fake: `Runner`, its turn counter, its filter hook and its error
propagation are the SDK's own — the three things this rests on and the three a
stubbed `Runner.run_streamed` would hide."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agents import Agent, function_tool
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseTextDeltaEvent,
)
from openai.types.responses.response_output_text import ResponseOutputText

from workspace_app.agent.context import Interjection
from workspace_app.api import Interjected, MaxTurnsExceeded, MessageDelta, RunError, ToolEnd
from workspace_app.api import litellm_runner as lr

from .test_litellm_runner import _ctx


@function_tool
def look() -> str:
    """Look at one more lot."""
    return "one more lot"


def _text(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="m",
        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
        role="assistant",
        status="completed",
        type="message",
    )


def _call(n: int) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        arguments=json.dumps({}), call_id=f"c{n}", name="look", type="function_call"
    )


class _Model(Model):
    """Looks once before the interjection and once after it, then answers —
    three model calls in all, one more than `max_turns=2` allows ONE segment.
    Records what each call was handed."""

    def __init__(self) -> None:
        self.inputs: list[list[Any]] = []

    def _next(self, items: Any) -> Any:
        self.inputs.append(items if isinstance(items, list) else [items])
        seen_q2 = "Q2" in json.dumps(items, default=str)
        looks_after = (
            sum(1 for i in (items if isinstance(items, list) else []) if _is_output(i)) - 1
        )
        if not seen_q2:
            return _call(len(self.inputs))
        if looks_after < 1:
            return _call(len(self.inputs))
        return _text("both answered")

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return ModelResponse(
            output=[self._next(_input(args, kwargs))], usage=Usage(), response_id=None
        )

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        out = self._next(_input(args, kwargs))
        response = Response(
            id="r",
            created_at=0,
            model="fake",
            object="response",
            output=[out],
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
        )
        if isinstance(out, ResponseOutputMessage):
            # A real stream says the text as deltas before it completes; the
            # runner's visible reply is built from those.
            yield ResponseTextDeltaEvent(
                type="response.output_text.delta",
                delta=out.content[0].text,
                item_id="m",
                output_index=0,
                content_index=0,
                logprobs=[],
                sequence_number=0,
            )
        yield ResponseCompletedEvent(
            type="response.completed", response=response, sequence_number=0
        )


def _input(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """`Model.get_response/stream_response(system_instructions, input, …)` — the
    SDK passes it positionally."""
    return kwargs["input"] if "input" in kwargs else args[1]


def _is_output(item: Any) -> bool:
    kind = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
    return kind == "function_call_output"


@pytest.mark.parametrize("stream", ["1", "0"])
async def test_a_message_sent_mid_turn_is_read_at_the_next_model_call(monkeypatch, stream):
    monkeypatch.setenv("WORKSPACE_AGENT_STREAM", stream)
    model = _Model()
    monkeypatch.setattr(
        lr, "_agent_for", lambda *a, **k: Agent(name="t", model=model, tools=[look])
    )
    ctx = _ctx()
    ctx.max_turns = 2
    ctx.kb_search_budget.used = 3
    ctx.wiki_search_budget.used = 2
    ctx.kb_grep_budget.used = 4
    asked: list[int] = []

    def take() -> list[Interjection]:
        asked.append(len(model.inputs))
        # Sent while the first look ran: there by the second model call.
        if len(model.inputs) == 1 and len(asked) == 2:
            return [
                Interjection(
                    content="Q2",
                    author="ann",
                    at=2,
                    speaker="Ann (ann)",
                    applied_skills=["lot-map"],
                )
            ]
        return []

    ctx.take_interjections = take

    events = [ev async for ev in lr.LitellmAgentRunner().run("Q1", ctx)]

    # Read at the boundary — after the first tool result, before the answer.
    kinds = [type(e).__name__ for e in events if isinstance(e, Interjected | ToolEnd)]
    assert kinds == ["ToolEnd", "Interjected", "ToolEnd"]
    assert [e for e in events if isinstance(e, Interjected)] == [
        Interjected(author="ann", created_at=2)
    ]
    # Q4: three model calls, max_turns=2 — only a fresh segment fits them.
    assert not any(isinstance(e, MaxTurnsExceeded) for e in events)
    assert "".join(e.text for e in events if isinstance(e, MessageDelta)) == "both answered"
    # The call after it was handed everything so far, then the message.
    second = model.inputs[1]
    assert _is_output(second[-2])
    assert second[-1] == {"role": "user", "content": "[Ann (ann)]: Q2"}
    # Q7: what the message carried goes with it; the search allowance is whole again.
    assert "lot-map" in ctx.applied_skills
    assert ctx.kb_search_budget.used == 0
    assert ctx.wiki_search_budget.used == 0
    assert ctx.kb_grep_budget.used == 0


async def test_a_turn_that_overflows_after_reading_a_message_still_recovers(monkeypatch):
    """§9 C12 (review round 1, F2), on the model-call retry #915 brought: after
    an interjection the turn continues from the history plus what it had done
    plus the message it read. The #624 recovery (`_HistoryTrim`, inside
    `CallRetryModel`) cuts the leading `len(ctx.history)` items of a call's
    input — so a segment's input must start with the history AS IT NOW IS.
    Here the first call is already cut before the message is read: a segment
    resumed from a snapshot of the SDK's (uncut) input would start with all ten
    old messages again, trim only the first few, and fail with nothing left to
    give up.

    Through the real `_agent_for` (the provider is the only fake), so the
    retry wrapper is the one production builds. Streamed only: the scripted
    provider has no one-shot shape."""
    from workspace_app.agent.context import AgentToolContext
    from workspace_app.resources import AgentConfig

    from ._fake_llm import Call, Fail, Say, ScriptedLlm

    class _Boom(Exception):
        pass

    calls: list[tuple[bool, int]] = []  # (the message was read, old messages sent)

    def judge(sent: list) -> Say | Call | Fail:
        # Too long while more than four old messages ride along — before the
        # message is read (the first call) as much as after it.
        history = [s for s in sent if "old " in json.dumps(s, default=str)]
        calls.append(("Q2" in json.dumps(sent, default=str), len(history)))
        if len(history) > 4:
            return Fail(_Boom("This model's maximum context length is 32768 tokens."))
        if "Q2" not in json.dumps(sent, default=str):
            return Call("lookup_user", {"handle": "x"})
        return Say(["both answered"])

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setenv("WORKSPACE_AGENT_STREAM", "1")
    monkeypatch.setattr(lr, "LitellmModel", ScriptedLlm(judge))
    ctx = AgentToolContext(
        investigation_id="i",
        agent_config=AgentConfig(name="t", model="m", system_prompt="s"),
        history=[{"role": "user", "content": f"old {i} " + "x" * 400} for i in range(10)],
    )
    asked: list[int] = []

    def take() -> list[Interjection]:
        asked.append(1)
        return [Interjection(content="Q2", author="ann", at=2)] if len(asked) == 2 else []

    ctx.take_interjections = take

    events = [ev async for ev in lr.LitellmAgentRunner(sleep=no_sleep).run("Q1", ctx)]

    # The re-send after each rejection carried less history, the segment after
    # the message was read went out already cut, and the turn got through.
    rejected_after_reading = [n for read, n in calls if read and n > 4]
    assert any(n > 4 for read, n in calls if not read)  # it was cut before reading
    assert rejected_after_reading == []
    assert not any(isinstance(e, RunError) for e in events)
    assert "".join(e.text for e in events if isinstance(e, MessageDelta)) == "both answered"


async def test_a_chat_turn_nobody_interjects_in_still_stops_at_the_step_limit(monkeypatch):
    """migrations.md #969: the step limit still stops a turn spinning by itself.
    A chat turn always has a reader; one that never returns a message must not
    restart the budget."""
    monkeypatch.setenv("WORKSPACE_AGENT_STREAM", "1")
    model = _Model()
    monkeypatch.setattr(
        lr, "_agent_for", lambda *a, **k: Agent(name="t", model=model, tools=[look])
    )
    ctx = _ctx()
    ctx.max_turns = 2
    ctx.take_interjections = list  # always: nothing was sent
    events = [ev async for ev in lr.LitellmAgentRunner().run("Q1", ctx)]
    assert any(isinstance(e, MaxTurnsExceeded) for e in events)
    assert len(model.inputs) == 2


async def test_a_turn_with_no_source_of_messages_runs_as_before(monkeypatch):
    """Every non-chat caller (a workflow step, a sub-agent, a KB probe) leaves
    `take_interjections` unset: one segment, the old step budget."""
    monkeypatch.setenv("WORKSPACE_AGENT_STREAM", "1")
    model = _Model()
    monkeypatch.setattr(
        lr, "_agent_for", lambda *a, **k: Agent(name="t", model=model, tools=[look])
    )
    ctx = _ctx()
    ctx.max_turns = 2
    events = [ev async for ev in lr.LitellmAgentRunner().run("Q1", ctx)]
    assert any(isinstance(e, MaxTurnsExceeded) for e in events)
    assert not any(isinstance(e, Interjected) for e in events)
