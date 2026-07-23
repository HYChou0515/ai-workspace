"""#613 P3: a per-chat goal — the user sets a completion condition; after each
turn a cheap LLM checks it and the chat auto-continues (bounded) until met.
These cover the storage row + the gated routes; the auto-continue driver has
its own specs in test_goal_autocontinue.py.
"""

from __future__ import annotations

from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.conversation_goal import (
    ConversationGoal,
    clear_goal,
    read_goal,
    register_conversation_goal,
    upsert_goal,
)
from workspace_app.resources.conversation_todos import (
    read_todos,
)
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


def test_goal_row_roundtrip_and_clear():
    spec = make_spec(default_user="u1")
    register_conversation_goal(spec)

    upsert_goal(spec, ConversationGoal(conversation_id="c1", condition="tests pass", set_by="u1"))
    stored = read_goal(spec, "c1")
    assert stored is not None
    assert (stored.condition, stored.set_by, stored.rounds_used, stored.state) == (
        "tests pass",
        "u1",
        0,
        "active",
    )

    clear_goal(spec, "c1")
    assert read_goal(spec, "c1") is None
    clear_goal(spec, "c1")  # idempotent


def _route_client(get_user_id=None, item_fields=None):
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec, **(item_fields or {}))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([RunDone()]),
        get_user_id=get_user_id or (lambda: "alice"),
    )
    return TestClient(app), spec, iid


def test_goal_routes_set_read_clear():
    """PUT sets an active goal (attributed to the signed-in user), GET reads it
    back (with the round budget so the panel can show k/N), DELETE clears it."""
    client, _spec, iid = _route_client()
    chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
    base = f"/a/rca/items/{iid}/chats/{chat['chat_id']}"

    first = client.get(f"{base}/goal").json()
    assert first["goal"] is None
    # This client wires NO checker LLM — the route must SAY so (the panel warns
    # instead of a set goal becoming a silently dead knob). The enabled=True
    # side is covered end-to-end in test_goal_autocontinue.py.
    assert first["checker_enabled"] is False

    r = client.put(f"{base}/goal", json={"condition": "the report is written"})
    assert r.status_code == 200
    goal = r.json()["goal"]
    assert goal["condition"] == "the report is written"
    assert goal["state"] == "active"
    assert goal["rounds_used"] == 0
    assert goal["set_by"] == "alice"
    assert goal["max_rounds"] >= 1

    assert client.get(f"{base}/goal").json()["goal"]["condition"] == "the report is written"

    assert client.delete(f"{base}/goal").status_code == 204
    assert client.get(f"{base}/goal").json()["goal"] is None


def test_goal_put_rejects_a_blank_condition():
    client, _spec, iid = _route_client()
    chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
    r = client.put(f"/a/rca/items/{iid}/chats/{chat['chat_id']}/goal", json={"condition": "   "})
    assert r.status_code == 422


def test_goal_routes_hide_a_private_item_from_a_non_reader():
    from workspace_app.perm.model import Permission

    client, _spec, iid = _route_client(
        get_user_id=lambda: "mallory",
        item_fields={"owner": "bob", "permission": Permission(visibility="private")},
    )
    assert client.get(f"/a/rca/items/{iid}/chats/x/goal").status_code == 404
    assert (
        client.put(f"/a/rca/items/{iid}/chats/x/goal", json={"condition": "c"}).status_code == 404
    )
    assert client.delete(f"/a/rca/items/{iid}/chats/x/goal").status_code == 404


def test_delete_chat_also_drops_its_goal_and_todos_rows():
    """Deleting a chat must not strand its point-key side rows (the todos row
    was P1's hole — locked here alongside the goal row)."""
    client, spec, iid = _route_client()
    chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
    rid = chat["chat_id"]
    base = f"/a/rca/items/{iid}/chats/{rid}"
    client.put(f"{base}/goal", json={"condition": "c"})
    client.put(f"{base}/todos", json={"items": [{"text": "a", "status": "pending"}]})
    assert read_goal(spec, rid) is not None
    assert read_todos(spec, rid) is not None

    assert client.delete(base).status_code == 204

    assert read_goal(spec, rid) is None
    assert read_todos(spec, rid) is None
