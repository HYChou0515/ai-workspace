"""#847 PR 3 P7 — markings sent with a message reach the AI.

The composer sends each named marking the user kept (a chip) with the message.
The send writes it to ``.markings/<name>.json`` through the file facade (so the
quota applies), records it on the persisted user message (so a reload still
shows the chip), and gives the model one line per marking. A marking whose
write is refused fails ITS chip, not the send.
"""

from __future__ import annotations

import json

import pytest

from tests.api._client import TestClient
from tests.api.conftest import register_rca_item
from workspace_app.api import RunDone, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.conversation import Conversation
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxBusy, SandboxNotFound


class _Capture:
    def __init__(self) -> None:
        self.prompt: str | None = None

    async def run(self, prompt, ctx):
        self.prompt = prompt
        yield RunDone()


def _app(runner, **kw):
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner, **kw
    )
    return app, spec


FAIL = {
    "name": "fail",
    "source": "/v/grid.ai.yaml",
    "columns": {"lot": ["L2", "L1"], "wafer": ["3", "4", "5"]},
}


def _user_messages(spec, iid):
    rm = spec.get_resource_manager(Conversation)
    [meta] = rm.search_resources(query=None)  # one item, one default chat
    conv = rm.get(meta.resource_id).data
    return [m for m in conv.messages if m.role == "user"]


def _perm_app(holder: dict[str, str]):
    """An app whose acting user is `holder["id"]` (review round 1, defect 1)."""
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Capture(),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _restricted_item(spec, **grants):
    from workspace_app.apps.rca.model import RcaInvestigation
    from workspace_app.perm import Permission

    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        return rm.create(
            RcaInvestigation(
                title="t",
                owner="bob",
                permission=Permission(visibility="restricted", **grants),
            )
        ).resource_id


def test_a_member_who_may_only_chat_cannot_write_a_file_through_a_marking():
    """Every other way into the workspace's files asks for a content-write verb;
    sending a message asks for `converse`. So a marking from someone who may
    chat but not write is refused on ITS chip — the message still goes."""
    holder = {"id": "bob"}
    client, spec = _perm_app(holder)
    carol = ["user:carol"]
    iid = _restricted_item(
        spec, read_meta=carol, read_content=carol, read_chat=carol, converse=carol
    )
    holder["id"] = "carol"
    assert (
        client.put(f"/a/rca/items/{iid}/files/.markings/fail.json", content=b"x").status_code == 403
    )

    r = client.post(f"/a/rca/items/{iid}/messages", json={"content": "q", "markings": [FAIL]})

    assert r.status_code == 202
    assert client.get(f"/a/rca/items/{iid}/files/.markings/fail.json").status_code == 404
    [msg] = _user_messages(spec, iid)
    [m] = msg.markings
    assert m.path == "" and m.error is not None


def test_adding_a_marking_file_asks_add_content_and_replacing_one_asks_edit_content():
    holder = {"id": "bob"}
    client, spec = _perm_app(holder)
    dan = ["user:dan"]
    iid = _restricted_item(
        spec,
        read_meta=dan,
        read_content=dan,
        read_chat=dan,
        converse=dan,
        add_content=dan,
    )
    holder["id"] = "dan"
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q1", "markings": [FAIL]})
    assert client.get(f"/a/rca/items/{iid}/files/.markings/fail.json").status_code == 200

    again = {**FAIL, "columns": {"lot": ["L9"]}}
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q2", "markings": [again]})

    first, second = _user_messages(spec, iid)
    assert first.markings[0].error is None
    assert second.markings[0].error is not None
    got = client.get(f"/a/rca/items/{iid}/files/.markings/fail.json")
    assert "L9" not in got.text


def test_a_reply_saved_while_the_markings_are_written_is_not_lost(monkeypatch):
    """Review round 1, defect 2: the route loads the conversation, then the send
    awaits the marking writes (a cold sandbox can take seconds). A previous
    turn's reply saved in that window must survive the user message's save."""
    from workspace_app.api import chat_send
    from workspace_app.resources.conversation import Message

    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)
    rm = spec.get_resource_manager(Conversation)
    real = chat_send.write_markings

    async def slow_write(files, workspace_id, markings, may_write):
        # Meanwhile, turn 1's on_complete re-reads the thread and saves its reply.
        [meta] = rm.search_resources(query=None)
        conv = rm.get(meta.resource_id).data
        conv.messages.append(Message(role="assistant", content="ANSWER-ONE"))
        rm.update(meta.resource_id, conv)
        return await real(files, workspace_id, markings, may_write)

    monkeypatch.setattr(chat_send, "write_markings", slow_write)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q", "markings": [FAIL]})

    [meta] = rm.search_resources(query=None)
    contents = [m.content for m in rm.get(meta.resource_id).data.messages]
    assert "ANSWER-ONE" in contents
    assert "q" in contents


def test_a_sent_marking_is_written_where_the_ai_can_read_it():
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)

    r = client.post(f"/a/rca/items/{iid}/messages", json={"content": "why?", "markings": [FAIL]})

    assert r.status_code == 202
    got = client.get(f"/a/rca/items/{iid}/files/.markings/fail.json")
    assert got.status_code == 200
    assert json.loads(got.content) == {
        "name": "fail",
        "sources": ["/v/grid.ai.yaml"],
        "columns": {"lot": ["L1", "L2"], "wafer": ["3", "4", "5"]},
    }


def test_the_model_gets_one_line_per_marking_naming_counts_and_path():
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)

    client.post(f"/a/rca/items/{iid}/messages", json={"content": "why?", "markings": [FAIL]})

    assert cap.prompt is not None
    assert "`fail`" in cap.prompt
    assert "lot: 2, wafer: 3" in cap.prompt
    assert ".markings/fail.json" in cap.prompt
    assert cap.prompt.rstrip().endswith("why?")


def test_the_marking_is_recorded_on_the_persisted_message_but_not_in_its_text():
    """A reload shows the chip; the text the user typed stays the text."""
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)

    client.post(f"/a/rca/items/{iid}/messages", json={"content": "why?", "markings": [FAIL]})

    [msg] = _user_messages(spec, iid)
    assert msg.content == "why?"
    [m] = msg.markings
    assert (m.name, m.path, m.counts, m.source, m.error) == (
        "fail",
        "/.markings/fail.json",
        {"lot": 2, "wafer": 3},
        "/v/grid.ai.yaml",
        None,
    )


def test_a_message_without_markings_writes_nothing():
    """A selection that is never sent writes nothing — the server only ever
    writes what a send carried."""
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)

    client.post(f"/a/rca/items/{iid}/messages", json={"content": "hi"})

    assert client.get(f"/a/rca/items/{iid}/files/.markings/fail.json").status_code == 404
    [msg] = _user_messages(spec, iid)
    assert msg.markings == []
    assert cap.prompt is not None and ".markings" not in cap.prompt


def test_a_full_workspace_fails_that_chip_not_the_send():
    cap = _Capture()
    app, spec = _app(cap, workspace_quota=100)
    client = TestClient(app)
    iid = register_rca_item(spec)
    assert client.put(f"/a/rca/items/{iid}/files/a.bin", content=b"x" * 90).status_code == 204
    small = {"name": "s", "source": None, "columns": {"k": ["v"]}}

    r = client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "why?", "markings": [FAIL, small]},
    )

    assert r.status_code == 202  # the send went through
    assert cap.prompt is not None and cap.prompt.rstrip().endswith("why?")
    [msg] = _user_messages(spec, iid)
    by_name = {m.name: m for m in msg.markings}
    assert by_name["fail"].error is not None and "full" in by_name["fail"].error
    assert by_name["fail"].path == ""
    # Only what was written is offered to the model.
    assert ".markings/fail.json" not in cap.prompt
    assert client.get(f"/a/rca/items/{iid}/files/.markings/fail.json").status_code == 404


def test_a_name_that_is_not_a_file_name_fails_its_chip():
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)
    bad = [
        {"name": n, "source": None, "columns": {"k": ["v"]}}
        for n in ("../escape", "a/b", ".hidden", "", "x" * 251)
    ]

    r = client.post(f"/a/rca/items/{iid}/messages", json={"content": "q", "markings": bad})

    assert r.status_code == 202
    [msg] = _user_messages(spec, iid)
    assert [m.error is not None for m in msg.markings] == [True] * 5
    assert client.get(f"/a/rca/items/{iid}/files/escape.json").status_code == 404
    assert cap.prompt is not None and ".markings" not in cap.prompt


def test_a_marking_with_no_values_is_not_a_marking():
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)

    client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "q", "markings": [{"name": "e", "source": None, "columns": {"k": []}}]},
    )

    [msg] = _user_messages(spec, iid)
    assert msg.markings == []


async def test_live_viewers_see_the_chips_the_reload_shows():
    """The broadcast carries the markings as persisted — a refused one with its
    reason — so a second viewer draws the same chips before any reload."""
    import asyncio

    from httpx import ASGITransport

    from tests.api._client import AsyncClient
    from workspace_app.api import MessageDelta, ScriptedAgentRunner

    app, spec = _app(ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]))
    iid = register_rca_item(spec)
    sub = app.state.turn_engine.subscribe(iid)
    seen: list = []

    async def collect():
        async for ev in sub:
            seen.append(ev)
            if getattr(ev, "type", None) == "done":
                return

    collector = asyncio.create_task(collect())
    bad = {"name": "a/b", "source": None, "columns": {"k": ["v"]}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/a/rca/items/{iid}/messages", json={"content": "q", "markings": [FAIL, bad]}
        )
        assert r.status_code == 202, r.text
    await asyncio.wait_for(collector, 3)

    um = next(e for e in seen if type(e).__name__ == "UserMessage")
    assert [(m["name"], m["path"], m["error"] is None) for m in um.markings] == [
        ("fail", "/.markings/fail.json", True),
        ("a/b", "", False),
    ]


def test_a_name_is_measured_in_bytes_as_a_file_name_is():
    # Round 15 defect lens: 100 emoji passed a 100-character cap as 400 bytes,
    # past a file name's 255, and the write crashed the send.
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)
    long_bytes = {"name": "😀" * 100, "source": None, "columns": {"k": ["v"]}}
    cjk = {"name": "批" * 80, "source": None, "columns": {"k": ["v"]}}  # 240 bytes: fits

    r = client.post(
        f"/a/rca/items/{iid}/messages", json={"content": "q", "markings": [long_bytes, cjk]}
    )

    assert r.status_code == 202
    [msg] = _user_messages(spec, iid)
    by_name = {m.name: m for m in msg.markings}
    assert by_name[long_bytes["name"]].error is not None
    assert by_name[cjk["name"]].error is None and by_name[cjk["name"]].path


@pytest.mark.parametrize(
    "exc",
    [
        OSError(36, "File name too long", "/srv/sandbox/abc/root/.markings/x.json"),
        IsADirectoryError(21, "Is a directory", "/srv/sandbox/abc/root/.markings/x.json"),
        SandboxNotFound("sandbox gone"),
        SandboxBusy("draining"),
    ],
    ids=["name-too-long", "a-directory", "sandbox-gone", "sandbox-busy"],
)
def test_a_write_the_workspace_refuses_fails_that_chip_not_the_send(monkeypatch, exc):
    # Round 15 defect lens: only a full workspace failed the chip; any other
    # refused write was a 500 and the typed question was lost.
    from workspace_app.files import WorkspaceFiles

    real = WorkspaceFiles.write

    async def refuse(self, workspace_id, path, data, *a, **k):
        if path.startswith("/.markings/"):
            raise exc
        return await real(self, workspace_id, path, data, *a, **k)

    monkeypatch.setattr(WorkspaceFiles, "write", refuse)
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)

    r = client.post(f"/a/rca/items/{iid}/messages", json={"content": "why?", "markings": [FAIL]})

    assert r.status_code == 202
    assert cap.prompt is not None and cap.prompt.rstrip().endswith("why?")
    [msg] = _user_messages(spec, iid)
    [m] = msg.markings
    assert m.error and m.path == ""
    assert "/srv/sandbox" not in m.error  # no sandbox internals on the chip
