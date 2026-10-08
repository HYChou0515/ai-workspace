"""Answering an `ask_user` question (grill-me).

The agent's question is a tool call; the answer is an ordinary user message
that additionally records **which** question it answers. That id is what lets
the UI attach the answer to its question, retire the buttons once used, and
survive the user answering an older question after a newer one was asked — all
of which degrade into guessing-by-adjacency without it.

The answer still goes through the normal send path: it starts a turn like any
other message. Nothing waits for it, so there is no second consumer to
coordinate with.
"""

from __future__ import annotations

from specstar import QB

from workspace_app.resources import Conversation

from .conftest import Harness


def _thread(harness: Harness):
    rm = harness.spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == harness.iid
    )
    return conv.messages


def _send(harness: Harness, content: str, answers: str | None = None):
    body: dict[str, object] = {"content": content}
    if answers is not None:
        body["answers"] = answers
    return harness.client.post(harness.wpath("/messages"), json=body)


def test_an_answer_records_which_question_it_answers(harness: Harness):
    assert _send(harness, "SQLite", answers="call_abc").status_code in (200, 202)

    answer = next(m for m in _thread(harness) if m.role == "user")
    assert answer.content == "SQLite"
    assert answer.answers == "call_abc"


def test_an_ordinary_message_records_no_question(harness: Harness):
    """The field is opt-in — every existing send path keeps working and must
    not start claiming to answer something."""
    assert _send(harness, "just talking").status_code in (200, 202)

    msg = next(m for m in _thread(harness) if m.role == "user")
    assert msg.answers is None


def test_an_answer_still_starts_a_turn(harness: Harness):
    """An answer is not a special channel — it is a message. The agent picks it
    up on the next turn, which is the whole reason nothing has to wait."""
    _send(harness, "SQLite", answers="call_abc")

    assert any(m.role == "assistant" for m in _thread(harness)), (
        "answering produced no assistant turn"
    )


async def test_live_viewers_learn_which_call_a_message_answers():
    """The broadcast carries `answers`, so a second viewer's card retires the
    moment someone else answers it — not on their next reload, after pressing
    a send the server then refuses (plan-outside-lookup)."""
    import asyncio

    from httpx import ASGITransport

    from tests.api._client import AsyncClient
    from tests.api.conftest import register_rca_item
    from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
    )
    iid = register_rca_item(spec)
    sub = app.state.turn_engine.subscribe(iid)
    seen: list = []

    async def collect():
        async for ev in sub:
            seen.append(ev)
            if getattr(ev, "type", None) == "done":
                return

    collector = asyncio.create_task(collect())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/a/rca/items/{iid}/messages", json={"content": "a", "answers": "c1"})
        assert r.status_code == 202, r.text
    await asyncio.wait_for(collector, 3)

    um = next(e for e in seen if type(e).__name__ == "UserMessage")
    assert um.answers == "c1"
