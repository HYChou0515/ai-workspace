"""#847 PR 3 P7 — markings sent with a message reach the AI.

The composer sends each named marking the user kept (a chip) with the message.
The send writes it to ``.markings/<name>.json`` through the file facade (so the
quota applies), records it on the persisted user message (so a reload still
shows the chip), and gives the model one line per marking. A marking whose
write is refused fails ITS chip, not the send.
"""

from __future__ import annotations

import json

from tests.api._client import TestClient
from tests.api.conftest import register_rca_item
from workspace_app.api import RunDone, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.conversation import Conversation
from workspace_app.sandbox.mock import MockSandbox


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
        for n in ("../escape", "a/b", ".hidden", "", "x" * 101)
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
