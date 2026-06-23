"""Chat-scoped HTTP routes (Phase 7, manual §3) — list / create individual chats,
and send / stream / cancel per chat. Item-level (no chat_id) endpoints keep hitting
the implicit default chat (byte-for-byte, covered in test_messages.py)."""

import asyncio

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, Message, make_spec
from workspace_app.sandbox.mock import MockSandbox

from .conftest import register_rca_item


def _client(runner=None):
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner or ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        get_user_id=lambda: "alice",
    )
    return TestClient(app), spec, iid


def _convs(spec, item_id):
    rm = spec.get_resource_manager(Conversation)
    return rm


def test_get_conversation_wire_field_is_item_id():
    """#139: the FE hydrates the shared RCA chat by listing ``GET /conversation``
    and matching the owning item on the ``item_id`` field (was ``investigation_id``
    pre-#89). Lock the wire contract so a future struct rename can't silently
    leave the FE matching nothing — which made the whole chat history (everyone's,
    not just other users') fail to load on reload."""
    client, _spec, iid = _client()
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    entries = client.get("/conversation").json()
    assert entries, "the message should have created the item's default conversation"
    data = entries[0]["data"]
    assert data["item_id"] == iid
    assert "investigation_id" not in data


def test_chats_list_is_empty_then_shows_the_default_after_a_message():
    client, _spec, iid = _client()
    assert client.get(f"/a/rca/items/{iid}/chats").json() == []  # read-only: no chat yet
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    chats = client.get(f"/a/rca/items/{iid}/chats").json()
    assert len(chats) == 1
    assert chats[0]["is_default"] is True
    assert chats[0]["run_id"] is None
    assert chats[0]["message_count"] >= 1


def test_create_free_chat_returns_a_chat_id_and_appears_in_the_list():
    client, _spec, iid = _client()
    r = client.post(f"/a/rca/items/{iid}/chats", json={"title": "Side chat"})
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Side chat"
    assert body["run_id"] is None
    cid = body["chat_id"]
    assert cid in [c["chat_id"] for c in client.get(f"/a/rca/items/{iid}/chats").json()]


def test_two_free_chats_receive_messages_independently():
    client, spec, iid = _client()
    a = client.post(f"/a/rca/items/{iid}/chats", json={"title": "A"}).json()["chat_id"]
    b = client.post(f"/a/rca/items/{iid}/chats", json={"title": "B"}).json()["chat_id"]
    client.post(f"/a/rca/items/{iid}/chats/{a}/messages", json={"content": "to-A"})
    client.post(f"/a/rca/items/{iid}/chats/{b}/messages", json={"content": "to-B"})
    rm = _convs(spec, iid)
    a_user = [m.content for m in rm.get(a).data.messages if m.role == "user"]
    b_user = [m.content for m in rm.get(b).data.messages if m.role == "user"]
    assert a_user == ["to-A"]
    assert b_user == ["to-B"]


def test_item_level_message_keeps_hitting_the_default_chat():
    client, spec, iid = _client()
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "first"})  # creates default
    chats = client.get(f"/a/rca/items/{iid}/chats").json()
    default_id = next(c["chat_id"] for c in chats if c["is_default"])
    side = client.post(f"/a/rca/items/{iid}/chats", json={"title": "side"}).json()["chat_id"]
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "second"})  # item-level → default
    rm = _convs(spec, iid)
    assert [m.content for m in rm.get(default_id).data.messages if m.role == "user"] == [
        "first",
        "second",
    ]
    assert [m for m in rm.get(side).data.messages if m.role == "user"] == []


def test_chat_scoped_message_404s_for_an_unknown_chat():
    client, _spec, iid = _client()
    r = client.post(f"/a/rca/items/{iid}/chats/conversation:nope/messages", json={"content": "x"})
    assert r.status_code == 404


def test_chat_scoped_message_404s_for_a_chat_of_another_item():
    client, spec, iid = _client()
    other = register_rca_item(spec)
    foreign = spec.get_resource_manager(Conversation).create(
        Conversation(item_id=other, created_ms=1)
    )
    r = client.post(
        f"/a/rca/items/{iid}/chats/{foreign.resource_id}/messages", json={"content": "x"}
    )
    assert r.status_code == 404


def test_chat_scoped_cancel_is_a_noop_when_idle():
    client, _spec, iid = _client()
    cid = client.post(f"/a/rca/items/{iid}/chats", json={"title": "c"}).json()["chat_id"]
    r = client.delete(f"/a/rca/items/{iid}/chats/{cid}/messages/current")
    assert r.status_code == 204


def _seed_turns(rm, item_id: str, title: str = "c") -> str:
    """Create a chat with two whole turns (user + assistant each) and return its id."""
    rev = rm.create(Conversation(item_id=item_id, title=title, created_ms=1))
    conv = rm.get(rev.resource_id).data
    conv.messages = [
        Message(role="user", content="q1"),
        Message(role="assistant", content="a1"),
        Message(role="user", content="q2"),
        Message(role="assistant", content="a2"),
    ]
    rm.update(rev.resource_id, conv)
    return rev.resource_id


def test_chat_scoped_undo_drops_the_last_whole_turn():
    client, spec, iid = _client()
    rm = _convs(spec, iid)
    cid = _seed_turns(rm, iid)
    r = client.delete(f"/a/rca/items/{iid}/chats/{cid}/messages", params={"turns": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["removed"] == 2  # the user prompt + its assistant reply
    assert body["message_count"] == 2
    left = [(m.role, m.content) for m in rm.get(cid).data.messages]
    assert left == [("user", "q1"), ("assistant", "a1")]


def test_chat_scoped_undo_more_turns_than_exist_clears_the_chat():
    client, spec, iid = _client()
    rm = _convs(spec, iid)
    cid = _seed_turns(rm, iid)
    r = client.delete(f"/a/rca/items/{iid}/chats/{cid}/messages", params={"turns": 9})
    assert r.status_code == 200
    assert r.json() == {"message_count": 0, "removed": 4}
    assert rm.get(cid).data.messages == []


def test_chat_scoped_undo_requires_a_positive_turns():
    client, spec, iid = _client()
    cid = _seed_turns(_convs(spec, iid), iid)
    r = client.delete(f"/a/rca/items/{iid}/chats/{cid}/messages", params={"turns": 0})
    assert r.status_code == 422  # turns >= 1


def test_chat_scoped_undo_404s_for_an_unknown_chat():
    client, _spec, iid = _client()
    r = client.delete(f"/a/rca/items/{iid}/chats/conversation:nope/messages", params={"turns": 1})
    assert r.status_code == 404


async def test_chat_scoped_stream_is_per_chat():
    """A non-default chat's turn streams on its OWN key; a message posted to that chat
    reaches its stream (manual §3, per-chat /stream)."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        get_user_id=lambda: "alice",
    )
    conv_rm = spec.get_resource_manager(Conversation)
    conv_rm.create(Conversation(item_id=iid, created_ms=1))  # default chat A (earliest)
    b = conv_rm.create(Conversation(item_id=iid, title="B", created_ms=2)).resource_id

    eng = app.state.turn_engine
    sub_b = eng.subscribe(b)  # B is non-default → its engine key is its own id
    seen: list = []

    async def collect():
        async for ev in sub_b:
            seen.append(ev)
            if getattr(ev, "type", None) == "done":
                return

    col = asyncio.create_task(collect())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post(f"/a/rca/items/{iid}/chats/{b}/messages", json={"content": "hi-B"})
    await asyncio.wait_for(col, 3)

    names = [type(e).__name__ for e in seen]
    assert "UserMessage" in names and "MessageDelta" in names
    um = next(e for e in seen if type(e).__name__ == "UserMessage")
    assert um.content == "hi-B"
