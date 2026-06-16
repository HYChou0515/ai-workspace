"""DELETE /a/{slug}/items/{id}/messages?turns=N — undo the last N turns (#38).

A "turn" is delimited by a user message: the prompt plus everything the
agent produced for it (assistant / tool / error markers) until the next
prompt. Undo removes the last N whole turns from the conversation; the
next turn's history then no longer includes them. File state is NOT
reverted (a separate follow-up) — undo only edits the conversation.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from specstar import QB

from workspace_app.api import (
    MessageDelta,
    RunDone,
    ScriptedAgentRunner,
    ToolEnd,
    ToolStart,
    create_app,
)
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from .conftest import register_rca_item


def _client():
    spec = make_spec(default_user="u")
    # Each turn: a tool call + an answer, so a turn is 3 messages
    # (user + tool + assistant) — exercises multi-message turns.
    runner = ScriptedAgentRunner(
        [
            ToolStart(call_id="c1", name="read_file", args={"path": "x"}),
            ToolEnd(call_id="c1", output="data"),
            MessageDelta(text="answer"),
            RunDone(),
        ]
    )
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    return TestClient(app), spec, register_rca_item(spec)


def _messages(spec, inv):
    rm = spec.get_resource_manager(Conversation)
    for r in rm.list_resources((QB["item_id"] == inv).build()):
        return list(r.data.messages)
    return []


def _send(client, inv, content):
    with client.stream("POST", f"/a/rca/items/{inv}/messages", json={"content": content}) as r:
        for _ in r.iter_lines():
            pass


def test_undo_one_turn_removes_the_whole_turn():
    client, spec, iid = _client()
    _send(client, iid, "q1")
    _send(client, iid, "q2")
    assert [m.role for m in _messages(spec, iid)] == [
        "user",
        "tool",
        "assistant",
        "user",
        "tool",
        "assistant",
    ]

    resp = client.request("DELETE", f"/a/rca/items/{iid}/messages", params={"turns": 1})
    assert resp.status_code == 200
    assert resp.json()["message_count"] == 3

    # The whole second turn is gone — no orphan tool/assistant left behind.
    msgs = _messages(spec, iid)
    assert [m.role for m in msgs] == ["user", "tool", "assistant"]
    assert msgs[0].content == "q1"


def test_undo_multiple_turns():
    client, spec, iid = _client()
    for q in ("q1", "q2", "q3"):
        _send(client, iid, q)

    resp = client.request("DELETE", f"/a/rca/items/{iid}/messages", params={"turns": 2})
    assert resp.status_code == 200

    msgs = _messages(spec, iid)
    assert [m.content for m in msgs if m.role == "user"] == ["q1"]


def test_undo_more_turns_than_exist_clears_the_conversation():
    client, spec, iid = _client()
    _send(client, iid, "q1")

    resp = client.request("DELETE", f"/a/rca/items/{iid}/messages", params={"turns": 5})
    assert resp.status_code == 200
    assert resp.json()["message_count"] == 0
    assert _messages(spec, iid) == []


def test_undo_zero_or_negative_is_rejected():
    client, _, iid = _client()
    _send(client, iid, "q1")
    for n in (0, -1):
        resp = client.request("DELETE", f"/a/rca/items/{iid}/messages", params={"turns": n})
        assert resp.status_code == 422
