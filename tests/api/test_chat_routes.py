"""Chat-scoped HTTP routes (Phase 7, manual §3) — list / create individual chats,
and send / stream / cancel per chat. Item-level (no chat_id) endpoints keep hitting
the implicit default chat (byte-for-byte, covered in test_messages.py)."""

import asyncio
import json

from httpx import ASGITransport

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, Message, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient, TestClient
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


def test_get_conversation_list_filters_by_indexed_item_id():
    """The FE narrows ``GET /conversation`` to ONE item server-side via a
    data_conditions filter on the INDEXED ``item_id`` field — a perf fix so it no
    longer fetches the whole collection to scan on the client. Prove the route
    honours the filter (and that ``eq`` on the string handle works): with two
    items each holding a conversation, filtering to one returns only its row."""
    client, spec, iid = _client()
    other = register_rca_item(spec)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    client.post(f"/a/rca/items/{other}/messages", json={"content": "q2"})
    # Unfiltered, the collection holds BOTH items' conversations.
    assert len(client.get("/conversation").json()) == 2
    # Filtered to `iid`, the backend returns only that item's conversation —
    # an indexed WHERE, not a full scan the client has to narrow.
    conds = json.dumps([{"field_path": "item_id", "operator": "eq", "value": iid}])
    entries = client.get("/conversation", params={"data_conditions": conds}).json()
    assert [e["data"]["item_id"] for e in entries] == [iid]


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


# ── Multi-chat list UX (issue #132) ──────────────────────────────────────────


def test_chat_list_carries_a_name_hint_from_the_first_user_message():
    """An unnamed free chat shows the first user message (truncated) so the FE can
    label it without fetching the thread (#132 — first-message-snippet naming)."""
    client, _spec, iid = _client()
    cid = client.post(f"/a/rca/items/{iid}/chats", json={}).json()["chat_id"]
    assert client.get(f"/a/rca/items/{iid}/chats").json()[0]["name_hint"] == ""
    client.post(
        f"/a/rca/items/{iid}/chats/{cid}/messages",
        json={"content": "  Compare Q3 and Q4 yield rates please  "},
    )
    info = next(c for c in client.get(f"/a/rca/items/{iid}/chats").json() if c["chat_id"] == cid)
    assert info["name_hint"] == "Compare Q3 and Q4 yield rates please"


def test_chat_list_carries_the_driving_run_status_for_a_workflow_chat():
    """A workflow chat surfaces its `WorkflowRun.status` so the list can show a
    badge (●running / ⏸awaiting / ✓done) without polling each run (#132). A free
    chat has no status."""
    from workspace_app.workflow.run import RunStatus, WorkflowRun

    client, spec, iid = _client()
    run = spec.get_resource_manager(WorkflowRun).create(
        WorkflowRun(item_id=iid, captured_user="u", status=RunStatus.RUNNING)
    )
    spec.get_resource_manager(Conversation).create(
        Conversation(item_id=iid, run_id=run.resource_id, title="→memory", created_ms=5)
    )
    free = client.post(f"/a/rca/items/{iid}/chats", json={}).json()["chat_id"]
    by_id = {c["chat_id"]: c for c in client.get(f"/a/rca/items/{iid}/chats").json()}
    wf = next(c for c in by_id.values() if c["run_id"] == run.resource_id)
    assert wf["status"] == "running"
    assert by_id[free]["status"] is None


def test_chat_list_orders_by_recent_activity_and_does_not_pin_the_default():
    """The list is most-recent-activity first; the (still-flagged) default chat is
    NOT pinned to the top (#132 — no "main chat" privilege)."""
    client, _spec, iid = _client()
    a = client.post(f"/a/rca/items/{iid}/chats", json={"title": "A"}).json()["chat_id"]
    b = client.post(f"/a/rca/items/{iid}/chats", json={"title": "B"}).json()["chat_id"]
    # B is born after A → newest. A is the earliest free chat → the default, but the
    # default no longer leads the list.
    listed = client.get(f"/a/rca/items/{iid}/chats").json()
    assert listed[0]["chat_id"] == b
    assert next(c for c in listed if c["chat_id"] == a)["is_default"] is True
    # Activity (updated_time), not birth, drives order: touch A → A leads.
    client.post(f"/a/rca/items/{iid}/chats/{a}/messages", json={"content": "hi"})
    assert client.get(f"/a/rca/items/{iid}/chats").json()[0]["chat_id"] == a


def test_rename_chat_sets_its_title():
    """Manual rename (#132): PATCH a chat's title; the new title shows in the list."""
    client, _spec, iid = _client()
    cid = client.post(f"/a/rca/items/{iid}/chats", json={}).json()["chat_id"]
    r = client.patch(f"/a/rca/items/{iid}/chats/{cid}", json={"title": "Yield study"})
    assert r.status_code == 200
    assert r.json()["title"] == "Yield study"
    info = next(c for c in client.get(f"/a/rca/items/{iid}/chats").json() if c["chat_id"] == cid)
    assert info["title"] == "Yield study"


def test_rename_chat_404s_for_a_chat_of_another_item():
    client, spec, iid = _client()
    other = register_rca_item(spec)
    foreign = spec.get_resource_manager(Conversation).create(
        Conversation(item_id=other, created_ms=1)
    )
    r = client.patch(f"/a/rca/items/{iid}/chats/{foreign.resource_id}", json={"title": "x"})
    assert r.status_code == 404


def test_delete_free_chat_removes_it_from_the_list():
    """Delete a chat from the manage modal (#132)."""
    client, _spec, iid = _client()
    cid = client.post(f"/a/rca/items/{iid}/chats", json={"title": "x"}).json()["chat_id"]
    assert client.delete(f"/a/rca/items/{iid}/chats/{cid}").status_code == 204
    assert cid not in [c["chat_id"] for c in client.get(f"/a/rca/items/{iid}/chats").json()]


def test_delete_workflow_chat_cancels_its_run_then_removes_it():
    """Deleting a workflow chat cancels its driving run first (#132 — delete also
    cancels the run), then drops the conversation."""
    from workspace_app.workflow.run import RunStatus, WorkflowRun

    client, spec, iid = _client()
    run = spec.get_resource_manager(WorkflowRun).create(
        WorkflowRun(item_id=iid, captured_user="u", status=RunStatus.RUNNING)
    )
    cid = (
        spec.get_resource_manager(Conversation)
        .create(Conversation(item_id=iid, run_id=run.resource_id, created_ms=5))
        .resource_id
    )
    calls: list = []

    async def spy(run_id, item_id):
        calls.append((run_id, item_id))
        return True

    client.app.state.workflow_orchestrator.cancel = spy
    assert client.delete(f"/a/rca/items/{iid}/chats/{cid}").status_code == 204
    assert calls == [(run.resource_id, iid)]
    assert cid not in [c["chat_id"] for c in client.get(f"/a/rca/items/{iid}/chats").json()]


def test_delete_chat_404s_for_a_chat_of_another_item():
    client, spec, iid = _client()
    other = register_rca_item(spec)
    foreign = spec.get_resource_manager(Conversation).create(
        Conversation(item_id=other, created_ms=1)
    )
    assert client.delete(f"/a/rca/items/{iid}/chats/{foreign.resource_id}").status_code == 404


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


def test_export_chat_ships_the_chat_you_asked_for_not_the_default():
    """The Export button downloads the conversation on screen. It used to be
    item-scoped, so it resolved the item's DEFAULT chat and handed you the
    earliest one whatever you were reading — a plausible file, quietly wrong."""
    from workspace_app.kb.chat_export import parse_chat_export

    client, _spec, iid = _client()
    a = client.post(f"/a/rca/items/{iid}/chats", json={"title": "A"}).json()["chat_id"]
    b = client.post(f"/a/rca/items/{iid}/chats", json={"title": "B"}).json()["chat_id"]
    client.post(f"/a/rca/items/{iid}/chats/{a}/messages", json={"content": "to-A"})
    client.post(f"/a/rca/items/{iid}/chats/{b}/messages", json={"content": "to-B"})

    resp = client.get(f"/a/rca/items/{iid}/chats/{b}/export-chat")
    assert resp.status_code == 200, resp.text
    _title, messages = parse_chat_export(resp.content)
    assert [m["content"] for m in messages if m["role"] == "user"] == ["to-B"]


def test_export_chat_titles_the_file_after_the_chat():
    """The payload title and the download filename must name the CHAT. Titling
    them after the item made every export of one item look identical, and left a
    wrong-chat download with nothing to give itself away with."""
    from workspace_app.kb.chat_export import parse_chat_export

    client, _spec, iid = _client()
    b = client.post(f"/a/rca/items/{iid}/chats", json={"title": "Second chat"}).json()["chat_id"]
    client.post(f"/a/rca/items/{iid}/chats/{b}/messages", json={"content": "to-B"})

    resp = client.get(f"/a/rca/items/{iid}/chats/{b}/export-chat")
    title, _messages = parse_chat_export(resp.content)
    assert title == "Second chat"
    # RFC 6266: an ASCII `filename` every client understands, plus the UTF-8
    # `filename*` current browsers prefer. Both name the chat.
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"Second-chat.chat.json\"; filename*=UTF-8''Second-chat.chat.json"
    )


def test_export_carries_every_field_a_message_persists():
    """The file is for debugging, so it is a faithful archive of the thread: the
    model's own reasoning, when it answered, who it was, and what the reply cost
    all go in. Naming fields one at a time is what let three of them stand in for
    sixteen — a field was added, the export was not updated, and nothing said so."""
    from workspace_app.kb.chat_export import parse_chat_export
    from workspace_app.resources.conversation import MessageMetrics

    client, spec, iid = _client()
    chat_id = (
        _convs(spec, iid)
        .create(
            Conversation(
                item_id=iid,
                title="Rich chat",
                messages=[
                    Message(
                        role="assistant",
                        content="the answer",
                        author="agent",
                        reasoning="the model's own thinking",
                        created_at=1788,
                        metrics=MessageMetrics(
                            prompt_tokens=10, completion_tokens=7, elapsed_ms=1200
                        ),
                    )
                ],
            )
        )
        .resource_id
    )

    resp = client.get(f"/a/rca/items/{iid}/chats/{chat_id}/export-chat")
    assert resp.status_code == 200, resp.text
    _title, messages = parse_chat_export(resp.content)
    m = messages[0]
    assert m["reasoning"] == "the model's own thinking"
    assert m["created_at"] == 1788
    assert m["author"] == "agent"
    # The values this test set, not the whole dict: pinning the exact key set
    # would re-create the coupling this change removes — `MessageMetrics` grew an
    # `exact` field in #739 and the export carried it without anyone editing the
    # export, which is the entire point.
    assert m["metrics"]["prompt_tokens"] == 10
    assert m["metrics"]["completion_tokens"] == 7
    assert m["metrics"]["elapsed_ms"] == 1200
    # Nothing is substituted for an absent value. The old export forced a
    # missing `tool_name` to "", and the same habit applied to the metrics #749
    # is about to widen would write a 0 for "not measured" — the invented number
    # that PR exists to remove. Dumping the message as it stands cannot do that.
    assert m["tool_name"] is None


def test_export_chat_survives_a_title_that_is_not_latin_1():
    """A Content-Disposition header is latin-1 on the wire, and Python's `\\w`
    keeps CJK — so naming the download after the chat turned a Chinese title into
    a header the server cannot encode. The users of this deployment name chats in
    Chinese, so this is the common case, not an edge one."""
    client, _spec, iid = _client()
    b = client.post(f"/a/rca/items/{iid}/chats", json={"title": "爐溫漂移檢討"}).json()["chat_id"]
    client.post(f"/a/rca/items/{iid}/chats/{b}/messages", json={"content": "to-B"})

    resp = client.get(f"/a/rca/items/{iid}/chats/{b}/export-chat")
    assert resp.status_code == 200, resp.text
    resp.headers["content-disposition"].encode("latin-1")  # must survive the wire


def test_export_chat_404s_on_a_chat_from_another_item():
    """A chat id is only meaningful inside its own item — accepting a foreign one
    would hand back another item's conversation past this item's access check."""
    client, spec, iid = _client()
    other = register_rca_item(spec, title="Other")
    foreign = client.post(f"/a/rca/items/{other}/chats", json={"title": "elsewhere"}).json()[
        "chat_id"
    ]
    assert client.get(f"/a/rca/items/{iid}/chats/{foreign}/export-chat").status_code == 404


def test_the_chat_reports_what_it_is_costing_before_any_turn_runs():
    """#739 P2: the usage bar hydrates on page load, like the todo panel — the
    number must exist at rest, not only while a turn streams. It is anchored on
    the provider's own reported count, so it includes the system prompt and tool
    schemas the estimator cannot see."""
    from workspace_app.resources.conversation import MessageMetrics

    client, spec, iid = _client()
    rm = spec.get_resource_manager(Conversation)
    conv = rm.create(
        Conversation(
            item_id=iid,
            created_ms=1,
            messages=[
                Message(role="user", content="問題"),
                Message(
                    role="assistant",
                    content="回答",
                    # `exact` says the PROVIDER reported these, which is what
                    # makes them an anchor. Without it they are one of our own
                    # substituted estimates and the gauge must not present them
                    # as measured (#739 review).
                    metrics=MessageMetrics(
                        prompt_tokens=9_000, completion_tokens=400, elapsed_ms=10, exact=True
                    ),
                ),
            ],
        )
    )
    r = client.get(f"/a/rca/items/{iid}/chats/{conv.resource_id}/context")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["used"] == 9_400
    assert got["measured"] is True
    # The DENOMINATOR's provenance travels beside the numerator's: `measured`
    # says whether a provider counted the prompt, `limit_source` says which rung
    # of the ceiling ladder produced `limit`. Only the field's presence is
    # asserted here — WHICH rung answers depends on the machine, because this
    # app's bundled preset is an `ollama_chat/*` name and litellm resolves those
    # by asking the daemon. Asserting `catalog` here passed on a laptop with
    # Ollama installed and failed on CI, which is a test describing its
    # environment rather than the product. The ladder's own behaviour is pinned
    # in `test_context_ladder_end_to_end.py`, over a model the registry knows
    # without a daemon.
    assert "limit_source" in got, got
