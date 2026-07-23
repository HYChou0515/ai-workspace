"""#613 P1: the agent's `update_todos` tool maintains a per-conversation todo
list — whole-overwrite semantics (Claude-style TodoWrite), three statuses,
persisted on the shared backend keyed by conversation id (point key, no scan).
"""

from __future__ import annotations

from typing import Any, cast

from agents import RunContextWrapper

import workspace_app.api.app as app_mod
from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import update_todos_impl
from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.conversation_todos import (
    read_todos,
    register_conversation_todos,
)
from workspace_app.sandbox.mock import MockSandbox


def _build_executor(monkeypatch):
    """Full app via ``create_app`` (capturing the executor for its turn-context
    builder) + one seeded item — the same scaffolding as the #429 P10 wiring
    tests, so these specs exercise the REAL wired builder."""
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


def test_every_workspace_app_grants_update_todos_by_default():
    """#613 locked decision: `update_todos` rides every app's `agent.tools`
    ceiling (tool-picker default-on; a preset can still switch it off). The
    `_template` scaffold carries it too so new apps inherit the grant."""
    from workspace_app.apps.manifest import load_app_manifest

    for slug in ["rca", "playground", "pm", "topic-hub"]:
        manifest = load_app_manifest(slug)
        assert "update_todos" in manifest.agent.tools, slug


def test_app_boot_registers_the_todos_model():
    """The lifespan registers `ConversationTodos` post-apply (like the other
    coordination models — no bare CRUD routes), so a turn's update_todos works
    on the app's spec without anyone remembering to register it."""
    from ._client import TestClient

    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    with TestClient(app):
        assert read_todos(spec, "never-written") is None  # registered ⇒ readable


async def test_chat_turn_carries_the_conversation_id(monkeypatch):
    """The chat turn builder threads the thread's Conversation id into the tool
    context, so `update_todos` knows which row to overwrite."""
    _spec, executor, item_id = _build_executor(monkeypatch)

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
        conversation_id="conv-9",
    )

    assert ctx.conversation_id == "conv-9"


async def test_workflow_turn_carries_no_conversation_id(monkeypatch):
    """Workflow agent nodes deliberately don't get the todo tool's context —
    workflow runs have their own progress UI (#613 locked decision)."""
    _spec, executor, item_id = _build_executor(monkeypatch)

    ctx = await executor._turn_ctx.build_workflow_turn(
        item_id,
        agent_config=None,
        run_subagent=_dummy_subagent,
        history_messages=[],
    )

    assert ctx.conversation_id is None


def test_update_todos_whole_overwrites_and_persists():
    """The tool call replaces the conversation's todo list wholesale, and the
    list is readable back from the shared backend by conversation id."""
    spec = make_spec(default_user="u1")
    register_conversation_todos(spec)
    ctx = RunContextWrapper(AgentToolContext(spec=spec, conversation_id="conv-1", acting_user="u1"))

    out = update_todos_impl(
        ctx,
        todos=[
            {"text": "read the failing test", "status": "completed"},
            {"text": "fix the bug", "status": "in_progress"},
            {"text": "run the suite", "status": "pending"},
        ],
    )

    assert not out.startswith("error:")
    stored = read_todos(spec, "conv-1")
    assert stored is not None
    assert [(t.text, t.status) for t in stored.items] == [
        ("read the failing test", "completed"),
        ("fix the bug", "in_progress"),
        ("run the suite", "pending"),
    ]


def test_update_todos_second_call_replaces_the_whole_list():
    """Whole-list REPLACE: the second call's list is the new truth — items not
    re-sent are gone, not merged."""
    spec = make_spec(default_user="u1")
    register_conversation_todos(spec)
    ctx = RunContextWrapper(AgentToolContext(spec=spec, conversation_id="conv-1", acting_user="u1"))
    update_todos_impl(ctx, todos=[{"text": "a", "status": "pending"}])

    update_todos_impl(ctx, todos=[{"text": "b", "status": "in_progress"}])

    stored = read_todos(spec, "conv-1")
    assert stored is not None
    assert [(t.text, t.status) for t in stored.items] == [("b", "in_progress")]


def test_update_todos_reports_unavailable_without_a_conversation():
    """A turn with no conversation context (workflow node, KB chat, bare test
    ctx) gets a plain unavailability error — nothing is written anywhere."""
    ctx = RunContextWrapper(AgentToolContext())
    out = update_todos_impl(ctx, todos=[{"text": "a", "status": "pending"}])
    assert out.startswith("error:")


def test_update_todos_notifies_the_turn_event_sink():
    """After a successful write the tool calls the turn's `on_todos_updated`
    sink with the new list, so the runner can stream a live event to the FE."""
    spec = make_spec(default_user="u1")
    register_conversation_todos(spec)
    seen: list[list[tuple[str, str]]] = []
    ctx = RunContextWrapper(
        AgentToolContext(
            spec=spec,
            conversation_id="conv-1",
            acting_user="u1",
            on_todos_updated=lambda items: seen.append([(t.text, t.status) for t in items]),
        )
    )

    update_todos_impl(ctx, todos=[{"text": "a", "status": "pending"}])

    assert seen == [[("a", "pending")]]


def test_update_todos_failure_does_not_notify_the_sink():
    """A rejected call (bad status) must not emit a stale/false live update."""
    spec = make_spec(default_user="u1")
    register_conversation_todos(spec)
    seen: list[object] = []
    ctx = RunContextWrapper(
        AgentToolContext(
            spec=spec,
            conversation_id="conv-1",
            acting_user="u1",
            on_todos_updated=lambda items: seen.append(items),
        )
    )

    update_todos_impl(ctx, todos=[cast("Any", {"text": "b", "status": "done"})])

    assert seen == []


def _route_client(get_user_id=None, item_fields=None):
    """TestClient + one rca item + a chat created via the chats route — the
    P2 read/edit endpoints under test live on the chat namespace."""
    from workspace_app.filestore.memory import MemoryFileStore

    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec, **(item_fields or {}))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([RunDone()]),
        get_user_id=get_user_id or (lambda: "alice"),
    )
    from ._client import TestClient

    return TestClient(app), spec, iid


def test_get_todos_is_an_empty_list_before_any_write():
    """The panel's initial hydration on a fresh chat is a benign empty list,
    not a 404 — no one has to special-case 'no row yet'."""
    client, _spec, iid = _route_client()
    chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
    r = client.get(f"/a/rca/items/{iid}/chats/{chat['chat_id']}/todos")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_put_todos_persists_and_reads_back():
    """A user edit (PUT) whole-overwrites the list; GET and the stored row both
    reflect it, and the write is attributed to the signed-in user."""
    client, spec, iid = _route_client()
    chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
    items = [
        {"text": "a", "status": "completed"},
        {"text": "b", "status": "pending"},
    ]

    r = client.put(f"/a/rca/items/{iid}/chats/{chat['chat_id']}/todos", json={"items": items})

    assert r.status_code == 200
    assert r.json() == {"items": items}
    assert client.get(f"/a/rca/items/{iid}/chats/{chat['chat_id']}/todos").json() == {
        "items": items
    }


def test_put_todos_rejects_an_unknown_status():
    """The wire schema is strict — a typo'd status is a 422, nothing written."""
    client, _spec, iid = _route_client()
    chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
    r = client.put(
        f"/a/rca/items/{iid}/chats/{chat['chat_id']}/todos",
        json={"items": [{"text": "a", "status": "done"}]},
    )
    assert r.status_code == 422


def test_todos_routes_hide_a_private_item_from_a_non_reader():
    """#262 conventions: no read_meta ⇒ 404 (the item's existence is not
    disclosed) — the todos routes are gated like every other chat route."""
    from workspace_app.perm.model import Permission

    client, spec, iid = _route_client(
        get_user_id=lambda: "mallory",
        item_fields={"owner": "bob", "permission": Permission(visibility="private")},
    )
    r_get = client.get(f"/a/rca/items/{iid}/chats/whatever/todos")
    r_put = client.put(
        f"/a/rca/items/{iid}/chats/whatever/todos",
        json={"items": [{"text": "a", "status": "pending"}]},
    )
    assert r_get.status_code == 404
    assert r_put.status_code == 404


def test_update_todos_rejects_an_unknown_status_and_writes_nothing():
    """A typo'd status is rejected with the allowed values named, and the
    stored list is untouched (no partial write)."""
    spec = make_spec(default_user="u1")
    register_conversation_todos(spec)
    ctx = RunContextWrapper(AgentToolContext(spec=spec, conversation_id="conv-1", acting_user="u1"))
    update_todos_impl(ctx, todos=[{"text": "a", "status": "pending"}])

    out = update_todos_impl(ctx, todos=[cast("Any", {"text": "b", "status": "done"})])

    assert out.startswith("error:")
    assert "pending" in out  # names the allowed statuses
    stored = read_todos(spec, "conv-1")
    assert stored is not None
    assert [(t.text, t.status) for t in stored.items] == [("a", "pending")]


def test_update_todos_is_in_the_default_workspace_toolset():
    """`build_tools(None)` (a turn whose config carries no allowed_tools — the
    bare/no-config fallback paths) hands out `_WORKSPACE_TOOLS`, a SECOND
    default set independent of the app.json ceiling. Keep the todo tool in
    both, or those turns silently lack it. (App-catalog turns materialize the
    ceiling and were never affected — the #613 live-probe failure that led
    here turned out to be the $ref schema + a truncating num_ctx.)"""
    from workspace_app.agent import build_tools

    names = [t.name for t in build_tools(None)]
    assert "update_todos" in names


def test_update_todos_schema_is_inline_with_an_enum_status():
    """Live-probe regression (qwen3:14b via Ollama): a `$defs`/`$ref` tool
    schema renders badly through local chat templates — the model either
    claims the tool "is not available" or emits `status` as a mangled object.
    The built tool's schema must be fully inlined, `$defs`-free, and carry the
    status enum so a small model can only produce valid statuses."""
    import json

    from workspace_app.agent import build_tools

    tool = next(t for t in build_tools(["update_todos"]) if t.name == "update_todos")
    schema = tool.params_json_schema
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    items = schema["properties"]["todos"]["items"]
    assert items["properties"]["status"]["enum"] == ["pending", "in_progress", "completed"]
