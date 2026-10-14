"""Where a tool call happened, and for whom (docs/plan-tool-usage-stats.md P3).

Each door that builds a turn's context names its surface — never inferred
from other fields — and the person a call counts against (§3.4). Each test
walks the real door and reads the context the runner was handed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from tests.api.test_tool_grant_doors import _build, _chat_ctx
from workspace_app.agent.context import AgentToolContext
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.resources.agent_config import AgentConfig
from workspace_app.workflow.run import WorkflowRun
from workspace_app.workflow.run_identity import RunIdentities

# ── chat ────────────────────────────────────────────────────────────────────


async def test_a_chat_turn_is_the_chat_surface(monkeypatch) -> None:  # noqa: ANN001
    spec, builder, _, _, iid = _build(monkeypatch, {})

    ctx = await _chat_ctx(spec, builder, iid)

    assert ctx.surface == "chat"


# ── workflow and schedule ───────────────────────────────────────────────────


async def test_a_workflow_step_is_the_workflow_surface_counted_against_its_starter(
    monkeypatch,  # noqa: ANN001
) -> None:
    _, _, executor, runner, iid = _build(monkeypatch, {})

    await executor.drive_turn(iid, "no-such-chat", "dave", "hello", None)

    ctx = runner.ctxs[-1]
    assert (ctx.surface, ctx.usage_user) == ("workflow", "dave")


def _run(spec, iid: str, *, from_schedule: bool, captured: str = "owner") -> str:  # noqa: ANN001
    return (
        spec.get_resource_manager(WorkflowRun)
        .create(WorkflowRun(item_id=iid, captured_user=captured, from_schedule=from_schedule))
        .resource_id
    )


async def test_a_scheduled_run_with_nobody_bound_counts_against_the_owner(monkeypatch) -> None:  # noqa: ANN001
    spec, _, executor, runner, iid = _build(monkeypatch, {})
    run_id = _run(spec, iid, from_schedule=True)

    await executor.drive_turn(iid, "no-such-chat", "owner", "hello", None, run_id=run_id)

    ctx = runner.ctxs[-1]
    assert (ctx.surface, ctx.usage_user) == ("schedule", "owner")


async def test_a_scheduled_run_someone_bound_counts_against_them(monkeypatch) -> None:  # noqa: ANN001
    spec, _, executor, runner, iid = _build(monkeypatch, {})
    run_id = _run(spec, iid, from_schedule=True)
    RunIdentities(spec).record(
        run_id,
        "carol",
        verb="execute",
        item_id=iid,
        profile="default",
        workflow_id="w",
        workflow_digest="d",
    )

    await executor.drive_turn(iid, "no-such-chat", "owner", "hello", None, run_id=run_id)

    ctx = runner.ctxs[-1]
    assert (ctx.surface, ctx.usage_user) == ("schedule", "carol")


async def test_a_run_started_by_hand_is_a_workflow_not_a_schedule(monkeypatch) -> None:  # noqa: ANN001
    spec, _, executor, runner, iid = _build(monkeypatch, {})
    run_id = _run(spec, iid, from_schedule=False, captured="erin")

    await executor.drive_turn(iid, "no-such-chat", "erin", "hello", None, run_id=run_id)

    ctx = runner.ctxs[-1]
    assert (ctx.surface, ctx.usage_user) == ("workflow", "erin")


async def test_starting_a_page_schedule_marks_the_run_as_a_schedule_s() -> None:
    from tests.api.test_page_schedule_start import _fire, _Locator, _Orchestrator

    orch = _Orchestrator()

    await _fire(_Locator(), orch)

    assert orch.starts[0]["from_schedule"] is True


def test_a_fired_schedule_s_run_is_recorded_as_the_schedule_s() -> None:
    """Through the real sweep: the run a schedule starts carries the mark the
    executor reads (the orchestrator stores what it was told)."""
    from specstar import QB

    from tests.api.test_schedule_overview import (
        ITEM_SCHEDULES,
        _client_and_spec,
        _fire,
        _item,
        _put,
        _schedules,
        _workflow,
    )

    holder = {"id": "bob"}
    client, spec, app = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    with client:
        _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
        _put(client, iid, ITEM_SCHEDULES, _schedules({"every": "hourly", "run": "w0"}))
        _fire(client, app, iid)

    runs = [r.data for r in spec.get_resource_manager(WorkflowRun).list_resources(QB.all())]
    assert runs and all(r.from_schedule for r in runs)


# ── sub-agent, KB sub-agent ────────────────────────────────────────────────


def test_a_sub_agent_is_the_sub_agent_surface_counted_against_its_parent() -> None:
    from workspace_app.api.subagent_run import _child_context
    from workspace_app.apps.subagents import SubagentDef

    parent = AgentToolContext(
        agent_config=AgentConfig(name="a", model="m", allowed_tools=["read_file"]),
        surface="chat",
        acting_user="alice",
        investigation_id="i1",
        app_slug="rca",
    )

    child = _child_context(parent, SubagentDef(name="d", description="", tools=["read_file"]))

    assert child.surface == "subagent"
    assert (child.acting_user, child.investigation_id, child.app_slug) == ("alice", "i1", "rca")


class _Recording(ScriptedAgentRunner):
    def __init__(self) -> None:
        super().__init__([MessageDelta(text="ok"), RunDone()])
        self.ctxs: list[Any] = []

    async def run(self, prompt, ctx):  # type: ignore[override]
        self.ctxs.append(ctx)
        async for ev in super().run(prompt, ctx):
            yield ev


async def test_the_knowledge_base_sub_agent_is_its_own_surface() -> None:
    from workspace_app.api.kb_chat_routes import answer_question

    runner = _Recording()

    await answer_question(
        runner,
        None,  # ty: ignore[invalid-argument-type]
        [],
        "q",
        agent_config=AgentConfig(name="kb", model="m", allowed_tools=["kb_search"]),
    )

    assert runner.ctxs[-1].surface == "kb_agent"


# ── a nested turn borrows its caller's who / app / item ────────────────────


async def test_a_nested_turn_with_no_one_named_counts_against_its_caller() -> None:
    """The KB sub-agent's context is built from scratch — no person, no item —
    but it runs INSIDE the parent's `ask_knowledge_base` call, so its calls are
    counted against the parent's person, App and item."""
    from agents.tool_context import ToolContext
    from agents.usage import Usage

    from workspace_app.agent.tool_usage import ToolUsageRecorder, wrap_with_usage

    rec = ToolUsageRecorder(day=lambda: "d")
    inner_ctx = AgentToolContext(tool_usage=rec, surface="kb_agent")

    async def inner_invoke(_ctx, _args):  # noqa: ANN001, ANN202
        return "passages"

    from agents import FunctionTool

    inner = wrap_with_usage(
        FunctionTool(
            name="kb_search", description="d", params_json_schema={}, on_invoke_tool=inner_invoke
        ),
        "builtin",
    )

    async def outer_invoke(_ctx, _args):  # noqa: ANN001, ANN202
        tctx = ToolContext(
            context=inner_ctx,
            tool_name="kb_search",
            tool_call_id="c2",
            tool_arguments="{}",
            usage=Usage(),
        )
        await inner.on_invoke_tool(tctx, "{}")
        return "answer"

    outer = wrap_with_usage(
        FunctionTool(
            name="ask_knowledge_base",
            description="d",
            params_json_schema={},
            on_invoke_tool=outer_invoke,
        ),
        "builtin",
    )
    outer_ctx = AgentToolContext(
        tool_usage=rec, surface="chat", acting_user="alice", investigation_id="i1", app_slug="rca"
    )
    await outer.on_invoke_tool(
        ToolContext(
            context=outer_ctx,
            tool_name="ask_knowledge_base",
            tool_call_id="c1",
            tool_arguments="{}",
            usage=Usage(),
        ),
        "{}",
    )

    rows = {r.tool: r for r in rec.drain()}
    kb = rows["kb_search"]
    assert set(kb.by_user) == {"alice"}
    assert set(kb.by_item) == {"i1"}
    assert set(kb.by_surface_app) == {"kb_agent∕rca"}


def test_a_knowledge_base_chat_turn_is_the_kb_chat_surface() -> None:
    from tests.api._client import TestClient
    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.kb.chunker import FixedTokenChunker
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.resources import make_spec
    from workspace_app.resources.kb import EMBED_DIM
    from workspace_app.sandbox.mock import MockSandbox

    runner = _Recording()
    app = create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    client = TestClient(app)
    cid = client.post("/kb/chats", json={"title": "t", "collection_ids": []}).json()["resource_id"]

    client.post(f"/kb/chats/{cid}/messages", json={"content": "q"})

    assert runner.ctxs[-1].surface == "kb_chat"


_ = (asyncio, json)
