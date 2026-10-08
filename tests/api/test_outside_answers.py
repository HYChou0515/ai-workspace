"""Answering the "請幫我查" card (docs/plan-outside-lookup.md D4, D7).

One request does both halves — save what the person brought back under
`lookups/`, then send the message that answers the card — so the thread can
never say "saved" for a file that is not there, nor hold a file no message
mentions.
"""

from __future__ import annotations

import pytest

from tests.api._client import TestClient
from tests.api.conftest import register_rca_item
from workspace_app.agent.outside_lookup import OUTSIDE_LOOKUP_MARKER
from workspace_app.api import (
    MessageDelta,
    RunDone,
    ScriptedAgentRunner,
    ToolEnd,
    ToolStart,
    create_app,
)
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.conversation import Conversation
from workspace_app.sandbox.mock import MockSandbox

_CARD = f'Asked.{OUTSIDE_LOOKUP_MARKER}{{"why": "need it", "query": "pandas 2.0 新功能"}}'


def _events():
    return [
        ToolStart(call_id="c1", name="ask_outside", args={"why": "need it", "query": "q"}),
        ToolEnd(call_id="c1", output=_CARD),
        MessageDelta(text="ok"),
        RunDone(),
    ]


def _app(holder: dict[str, str] | None = None, **kw):
    holder = holder or {"id": "u"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner(_events()),
        get_user_id=lambda: holder["id"],
        **kw,
    )
    return TestClient(app), spec


def _asked(**kw):
    """An item whose chat already holds the card (call `c1`)."""
    client, spec = _app(**kw)
    iid = register_rca_item(spec)
    assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"}).status_code == 202
    return client, spec, iid


def _answer(client, iid, *, files=None, **form):
    data = {"tool_call_id": "c1", "kind": "found", **form}
    return client.post(f"/a/rca/items/{iid}/outside-answers", data=data, files=files or [])


def _thread(spec):
    rm = spec.get_resource_manager(Conversation)
    [meta] = rm.search_resources(query=None)
    conv = rm.get(meta.resource_id).data
    assert isinstance(conv, Conversation)
    return conv.messages


def _answers(spec):
    return [m for m in _thread(spec) if m.role == "user" and m.answers == "c1"]


def _paths(client, iid) -> set[str]:
    tree = client.get(f"/a/rca/items/{iid}/tree").json()
    return {f["path"].lstrip("/") for f in tree["files"]} | {
        d.lstrip("/").rstrip("/") for d in tree["dirs"]
    }


def _read(client, iid, path: str) -> str:
    r = client.get(f"/a/rca/items/{iid}/files/{path}")
    assert r.status_code == 200, r.text
    return r.text


# ── found (D4) ──────────────────────────────────────────────────────────────


def test_what_was_found_is_saved_and_answers_the_card():
    client, spec, iid = _asked()

    r = _answer(
        client,
        iid,
        content="| v | 變更 |\n|---|---|\n| 2.0 | [Arrow](https://a.example) |",
        source_url="https://pandas.example/whatsnew",
        target="Google",
    )

    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert path.startswith("lookups/") and path.endswith(".md")
    saved = _read(client, iid, path)
    assert "pandas 2.0 新功能" in saved
    assert "need it" in saved
    assert "https://pandas.example/whatsnew" in saved
    assert "Google" in saved
    assert "| 2.0 | [Arrow](https://a.example) |" in saved
    [msg] = _answers(spec)
    assert path in msg.content
    assert "[Arrow](https://a.example)" in msg.content


def test_the_answer_starts_the_next_turn():
    client, spec, iid = _asked()

    _answer(client, iid, content="x")

    roles = [m.role for m in _thread(spec)]
    assert roles[-1] == "assistant" and roles.count("assistant") == 2


def test_attachments_go_beside_the_file_and_the_message_names_them():
    client, spec, iid = _asked()

    r = _answer(
        client,
        iid,
        content="see attached",
        files=[
            ("attachments", ("shot.png", b"\x89PNG..", "image/png")),
            ("attachments", ("../../etc/spec.pdf", b"%PDF", "application/pdf")),
        ],
    )

    assert r.status_code == 200, r.text
    body = r.json()
    stem = body["path"].removesuffix(".md")
    assert body["attachments"] == [f"{stem}/shot.png", f"{stem}/spec.pdf"]
    assert _read(client, iid, f"{stem}/spec.pdf") == "%PDF"
    [msg] = _answers(spec)
    assert f"{stem}/shot.png" in msg.content and f"{stem}/spec.pdf" in msg.content


def test_attachments_alone_are_an_answer():
    client, _spec, iid = _asked()

    r = _answer(client, iid, files=[("attachments", ("a.png", b"x", "image/png"))])

    assert r.status_code == 200, r.text


def test_nothing_brought_back_is_not_an_answer():
    client, spec, iid = _asked()

    r = _answer(client, iid, content="  ")

    assert r.status_code == 422
    assert _answers(spec) == []
    assert not any(p.startswith("lookups") for p in _paths(client, iid))


def test_a_second_lookup_on_the_same_day_gets_its_own_file():
    client, spec, iid = _asked()
    first = _answer(client, iid, content="one").json()["path"]
    # A second card with the same query, answered too.
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "again"})
    rm = spec.get_resource_manager(Conversation)
    [meta] = rm.search_resources(query=None)
    conv = rm.get(meta.resource_id).data
    for m in conv.messages:  # the scripted turn reuses `c1`; give the new card its own id
        if m.role == "tool" and m.tool_call_id == "c1" and m is conv.messages[-2]:
            m.tool_call_id = "c2"
    rm.update(meta.resource_id, conv)

    second = client.post(
        f"/a/rca/items/{iid}/outside-answers",
        data={"tool_call_id": "c2", "kind": "found", "content": "two"},
    ).json()["path"]

    assert first != second
    assert _read(client, iid, first).endswith("one\n")
    assert _read(client, iid, second).endswith("two\n")


def test_a_long_paste_is_excerpted_in_the_message_and_whole_in_the_file():
    client, spec, iid = _asked()
    long = "字" * 20_000

    path = _answer(client, iid, content=long).json()["path"]

    assert long in _read(client, iid, path)
    [msg] = _answers(spec)
    assert len(msg.content) < 6_000
    assert path in msg.content


# ── not found (D7) ──────────────────────────────────────────────────────────


def test_not_found_saves_nothing_and_says_so_with_the_reason():
    client, spec, iid = _asked()

    r = client.post(
        f"/a/rca/items/{iid}/outside-answers",
        data={"tool_call_id": "c1", "kind": "not_found", "reason": "公司擋了這個網站"},
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"path": None, "attachments": []}
    [msg] = _answers(spec)
    assert "沒查到" in msg.content and "公司擋了這個網站" in msg.content
    assert not any(p.startswith("lookups") for p in _paths(client, iid))


# ── what it answers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("call", ["nope", ""])
def test_only_a_card_in_this_chat_can_be_answered(call: str):
    client, spec, iid = _asked()

    r = client.post(
        f"/a/rca/items/{iid}/outside-answers",
        data={"tool_call_id": call, "kind": "found", "content": "x"},
    )

    assert r.status_code in (404, 422)
    assert _answers(spec) == []


def test_another_tool_s_call_is_not_a_card():
    client, spec = _app()
    iid = register_rca_item(spec)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    rm = spec.get_resource_manager(Conversation)
    [meta] = rm.search_resources(query=None)
    conv = rm.get(meta.resource_id).data
    for m in conv.messages:
        if m.role == "tool":
            m.tool_name = "exec"
    rm.update(meta.resource_id, conv)

    assert _answer(client, iid, content="x").status_code == 404


def test_a_refused_call_is_not_a_card():
    client, spec = _app()
    iid = register_rca_item(spec)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    rm = spec.get_resource_manager(Conversation)
    [meta] = rm.search_resources(query=None)
    conv = rm.get(meta.resource_id).data
    for m in conv.messages:
        if m.role == "tool":
            m.content = "error: pass exactly one of `query` or `url`"
    rm.update(meta.resource_id, conv)

    assert _answer(client, iid, content="x").status_code == 404


def test_a_card_is_answered_once():
    client, spec, iid = _asked()
    assert _answer(client, iid, content="one").status_code == 200

    r = _answer(client, iid, content="two")

    assert r.status_code == 409
    assert len(_answers(spec)) == 1
    assert len([p for p in _paths(client, iid) if p.endswith(".md")]) == 1


def test_a_named_chat_has_the_same_route():
    client, spec, iid = _asked()
    rm = spec.get_resource_manager(Conversation)
    [meta] = rm.search_resources(query=None)

    r = client.post(
        f"/a/rca/items/{iid}/chats/{meta.resource_id}/outside-answers",
        data={"tool_call_id": "c1", "kind": "found", "content": "x"},
    )

    assert r.status_code == 200, r.text
    assert len(_answers(spec)) == 1


# ── who may, and what a full workspace does ─────────────────────────────────


def _restricted_item(spec, **grants):
    from workspace_app.apps.rca.model import RcaInvestigation
    from workspace_app.perm import Permission

    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        return rm.create(
            RcaInvestigation(
                title="t", owner="bob", permission=Permission(visibility="restricted", **grants)
            )
        ).resource_id


def _as_carol(**grants):
    holder = {"id": "bob"}
    client, spec = _app(holder)
    carol = ["user:carol"]
    iid = _restricted_item(spec, read_meta=carol, read_content=carol, read_chat=carol, **grants)
    assert client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"}).status_code == 202
    holder["id"] = "carol"
    return client, spec, iid


def test_someone_who_may_only_read_cannot_answer():
    client, spec, iid = _as_carol()

    assert _answer(client, iid, content="x").status_code == 403
    assert _answers(spec) == []


def test_someone_who_may_chat_but_not_add_files_answers_without_a_file():
    """Sending asks `converse`; a file asks `add_content`, as every other way
    into the workspace does (#847's markings). The text still reaches the AI."""
    client, spec, iid = _as_carol(converse=["user:carol"])

    r = _answer(client, iid, content="found it: 42")

    assert r.status_code == 200, r.text
    assert r.json() == {"path": None, "attachments": []}
    [msg] = _answers(spec)
    assert "found it: 42" in msg.content
    assert not any(p.startswith("lookups") for p in _paths(client, iid))


def test_someone_who_may_chat_but_not_add_files_cannot_attach():
    client, spec, iid = _as_carol(converse=["user:carol"])

    r = _answer(client, iid, content="x", files=[("attachments", ("a.png", b"x", "image/png"))])

    assert r.status_code == 403
    assert _answers(spec) == []


def test_a_full_workspace_saves_nothing_and_sends_nothing():
    client, spec, iid = _asked(workspace_quota=10)

    r = _answer(client, iid, content="x" * 100)

    assert r.status_code == 507
    assert _answers(spec) == []
    assert not any(p.startswith("lookups") for p in _paths(client, iid))


def test_a_send_refused_after_the_file_landed_takes_the_file_back():
    """The file fits; then the workspace is full, and a full workspace refuses
    the turn (#538). The answer must not leave a file no message mentions."""
    # The quota is exactly the file: it fits, and leaves the workspace full.
    probe, _s, piid = _asked()
    size = len(_read(probe, piid, _answer(probe, piid, content="x" * 300).json()["path"]).encode())
    client, spec, iid = _asked(workspace_quota=size)

    r = _answer(client, iid, content="x" * 300)

    assert r.status_code == 507, r.text
    assert _answers(spec) == []
    assert not any(p.endswith(".md") for p in _paths(client, iid))
