"""Turns on the usage record (docs/plan-capacity-usage.md §5.2 `turn`, §6.3):
when a message was accepted, when its turn started, when the first event and
the first output went out, and how it ended — through the engine's own
`enqueue`, the one door every surface uses."""

from __future__ import annotations

import asyncio
import time

import pytest

from workspace_app.agent import AgentToolContext
from workspace_app.api import MessageDelta, RunDone
from workspace_app.api.events import AgentMetrics, ToolStart
from workspace_app.api.turns import ChatTurnEngine
from workspace_app.capacity.recorder import UsageRecorder
from workspace_app.capacity.scope import UsageScope, current
from workspace_app.capacity.sink import UsageSink


class _ListSink(UsageSink):
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


def _recorder(sink: _ListSink) -> UsageRecorder:
    return UsageRecorder(sink, component="api", pod="api-1", window_s=300, queue_max=1000)


class _Runner:
    """A metrics event first (what the real runner emits before the model
    answers), a pause, then output — so first event and first output differ."""

    def __init__(self, *, fail: bool = False, block: asyncio.Event | None = None) -> None:
        self.fail = fail
        self.block = block
        self.scopes: list[UsageScope | None] = []

    async def run(self, content, ctx):  # noqa: ANN001, ANN201
        self.scopes.append(current())
        yield AgentMetrics(phase="up")
        await asyncio.sleep(0.1)
        if self.block is not None:
            await self.block.wait()
        if self.fail:
            raise RuntimeError("model gateway down")
        yield ToolStart(call_id="c1", name="read_file", args={})
        yield MessageDelta(text="hello")
        yield AgentMetrics(
            phase="final",
            model="qwen3",
            measured_prompt_tokens=120,
            measured_completion_tokens=7,
        )
        yield RunDone()


def _ctx() -> AgentToolContext:
    return AgentToolContext(investigation_id="item-1", app_slug="pm", acting_user="alice")


def _usage(sink: _ListSink, kind: str) -> list[dict]:
    return [d["usage"] for d in sink.docs if d["usage"]["kind"] == kind]


async def _one_turn(runner: _Runner, **enqueue_kw) -> tuple[_ListSink, ChatTurnEngine]:
    sink = _ListSink()
    rec = _recorder(sink)
    engine = ChatTurnEngine(runner, usage_recorder=rec)  # ty: ignore[invalid-argument-type]
    fut = engine.enqueue("item-1", "hi", _ctx(), on_complete=lambda _m: None, **enqueue_kw)
    await asyncio.wait_for(fut, 5)
    await engine.forget("item-1")
    rec.close()
    return sink, engine


async def test_a_turn_is_recorded_with_its_moments_in_order():
    accepted = int(time.time() * 1000) - 2000
    sink, _ = await _one_turn(_Runner(), surface="chat", accepted_ms=accepted)

    (turn,) = _usage(sink, "turn")
    assert (turn["user"], turn["app"], turn["item"], turn["surface"], turn["outcome"]) == (
        "alice",
        "pm",
        "item-1",
        "chat",
        "done",
    )
    assert turn["accepted_ms"] == accepted
    assert accepted < turn["started_ms"] <= turn["first_event_ms"]
    assert turn["first_output_ms"] - turn["first_event_ms"] >= 100, "output came after the pause"
    assert turn["first_output_ms"] <= turn["ended_ms"]
    assert (turn["model"], turn["prompt_tokens"], turn["completion_tokens"]) == ("qwen3", 120, 7)


async def test_the_turn_runs_in_its_own_usage_scope():
    runner = _Runner()

    await _one_turn(runner, surface="kb")

    assert runner.scopes == [UsageScope(user="alice", app="pm", item="item-1", purpose="kb")]


async def test_a_turn_counts_as_work_while_it_runs_and_its_events_are_counted():
    sink, _ = await _one_turn(_Runner(), surface="workflow")

    work = {r["work"]: r for r in _usage(sink, "work")}
    assert work["turn:workflow"]["count"] == 1
    assert work["turn:workflow"]["seconds"] >= 0.1
    # agent_metrics, tool_start, message_delta, agent_metrics, run_done
    assert work["turn-events:workflow"]["count"] == 5
    assert {r["user"] for r in work.values()} == {"alice"}


async def test_a_failed_turn_is_recorded_as_an_error_without_output():
    sink, _ = await _one_turn(_Runner(fail=True))

    (turn,) = _usage(sink, "turn")
    assert turn["outcome"] == "error"
    assert turn["surface"] == "chat", "chat is the surface when none is named"
    assert "first_output_ms" not in turn
    assert turn["first_event_ms"] <= turn["ended_ms"]


async def test_a_stopped_turn_is_recorded_as_cancelled():
    sink = _ListSink()
    rec = _recorder(sink)
    block = asyncio.Event()
    engine = ChatTurnEngine(_Runner(block=block), usage_recorder=rec)  # ty: ignore[invalid-argument-type]
    fut = engine.enqueue("item-1", "hi", _ctx(), on_complete=lambda _m: None)
    await asyncio.sleep(0.3)
    await engine.cancel_current("item-1")
    await asyncio.wait_for(fut, 5)
    await engine.forget("item-1")
    rec.close()

    (turn,) = _usage(sink, "turn")
    assert turn["outcome"] == "cancelled"


@pytest.mark.parametrize(
    ("ctx", "user"),
    [
        (AgentToolContext(acting_user="alice", usage_user="schedule-binder"), "schedule-binder"),
        (AgentToolContext(acting_user="alice"), "alice"),
    ],
)
async def test_the_person_is_the_one_tool_usage_counts_for(ctx, user):
    """The same rule the tool-usage statistics use (`agent.tool_usage.who`): two
    pages must not disagree about whose turn it was."""
    sink = _ListSink()
    rec = _recorder(sink)
    engine = ChatTurnEngine(_Runner(), usage_recorder=rec)  # ty: ignore[invalid-argument-type]
    await asyncio.wait_for(engine.enqueue("k", "hi", ctx, on_complete=lambda _m: None), 5)
    await engine.forget("k")
    rec.close()

    assert [t["user"] for t in _usage(sink, "turn")] == [user]
