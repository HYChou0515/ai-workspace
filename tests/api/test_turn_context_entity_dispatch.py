"""#429 P10 wiring: the turn-context builder threads the event-dispatch sink into
every agent turn, and a workflow agent-node turn also carries the run's trigger
origin — so an agent editing an entity fires on_event workflows AND can't bypass
the recursion depth cap. Built end-to-end through ``create_app`` so the real
``EventTriggerDispatcher`` is the wired sink.
"""

from __future__ import annotations

import workspace_app.api.app as app_mod
from workspace_app.agent import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.entity.events import EntityOrigin
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.workflow.handle import WorkflowHandle


def _build(monkeypatch):
    """Build the full app, capturing the ``WorkflowExecutor``, and seed one item."""
    spec = make_spec()
    runner = ScriptedAgentRunner([RunDone()])
    captured: dict[str, object] = {}
    real = app_mod.WorkflowExecutor

    def _capture(**kw):
        ex = real(**kw)
        captured["ex"] = ex
        return ex

    monkeypatch.setattr(app_mod, "WorkflowExecutor", _capture)
    create_app(spec=spec, sandbox=MockSandbox(), filestore=SpecstarFileStore(spec), runner=runner)
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    return spec, captured["ex"], item_id


async def _dummy_subagent(*_a, **_k):
    return "", []


async def test_workflow_turn_carries_the_sink_and_the_passed_origin(monkeypatch):
    _spec, executor, item_id = _build(monkeypatch)

    ctx = await executor._turn_ctx.build_workflow_turn(
        item_id,
        agent_config=None,
        run_subagent=_dummy_subagent,
        history_messages=[],
        entity_write_origin=EntityOrigin(trigger="pm:echo:on_issue", depth=2),
    )

    assert ctx.entity_write_sink is not None  # app.py wired the real dispatcher
    assert ctx.entity_write_origin == EntityOrigin(trigger="pm:echo:on_issue", depth=2)


async def test_chat_turn_wires_the_sink_but_has_no_origin(monkeypatch):
    """A plain interactive chat carries the sink (an AI edit a user asks for fires
    triggers) but no ambient origin — it's a first-level write (depth 0)."""
    _spec, executor, item_id = _build(monkeypatch)

    ctx = await executor._turn_ctx.build_chat_turn(
        item_id,
        agent_config=None,
        run_subagent=_dummy_subagent,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="u",
        speaker=None,
    )

    assert ctx.entity_write_sink is not None
    assert ctx.entity_write_origin is None


async def test_wire_handle_threads_the_runs_origin_into_the_agent_turn(monkeypatch):
    """The end-to-end wiring the DoD rests on: a triggered run's handle carries an
    EntityOrigin; wire_handle binds wf.drive_turn so that origin reaches the agent
    turn's ctx — so an agent editing an entity mid-run is depth-counted like the
    workflow handle's own writes and can't slip past the depth cap."""
    _spec, executor, item_id = _build(monkeypatch)

    seen: dict[str, object] = {}
    real_enqueue = executor._turn_engine.enqueue

    def spy_enqueue(key, content, ctx, *, on_complete):
        seen["ctx"] = ctx
        return real_enqueue(key, content, ctx, on_complete=on_complete)

    monkeypatch.setattr(executor._turn_engine, "enqueue", spy_enqueue)

    wf = WorkflowHandle(
        store=executor._files,
        workspace_id=item_id,
        origin_trigger="pm:echo:on_issue",
        trigger_depth=2,
    )
    executor.wire_handle(wf, "run-1", item_id, "u", "no-such-chat")
    assert wf.drive_turn is not None  # wire_handle bound it (narrows DriveTurn | None)
    await wf.drive_turn("hi", None)

    ctx = seen["ctx"]
    assert isinstance(ctx, AgentToolContext)
    assert ctx.entity_write_origin == EntityOrigin(trigger="pm:echo:on_issue", depth=2)
    assert ctx.entity_write_sink is not None
