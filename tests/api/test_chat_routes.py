"""Chat-scoped HTTP routes (Phase 7, manual §3) — list / create individual chats,
and send / stream / cancel per chat. Item-level (no chat_id) endpoints keep hitting
the implicit default chat (byte-for-byte, covered in test_messages.py)."""

import asyncio

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
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
