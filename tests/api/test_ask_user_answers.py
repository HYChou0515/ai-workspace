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
