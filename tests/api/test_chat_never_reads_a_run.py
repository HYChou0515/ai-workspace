"""docs/plan-workflow-out-of-chat.md P3 — the chat never reads a run.

A chat turn's history used to be everything before the user's message,
filtered by role and size only, so a run's steps (and the steerer's JSON) sat in
the chat's context. A message a run persists now carries `run_id`; a chat
taken over under #343 holds older, unmarked run messages, placed by the run's
own start and end.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from workspace_app.api.run_windows import RunWindow, without_runs
from workspace_app.resources import Message


def _m(
    content: str, at: int | None, *, role: str = "assistant", run_id: str | None = None
) -> Message:
    return Message(role=role, content=content, created_at=at, run_id=run_id)


def test_a_message_a_run_marked_never_reaches_a_chat_turn() -> None:
    msgs = [_m("hi", 1, role="user"), _m("a step's answer", 2, run_id="r1"), _m("chat reply", 3)]

    assert [m.content for m in without_runs(msgs, [])] == ["hi", "chat reply"]


def test_a_taken_over_chat_drops_unmarked_messages_inside_a_finished_runs_window() -> None:
    msgs = [
        _m("prepared in chat", 10, role="user"),
        _m("legacy step answer", 25),
        _m("legacy steer json", 30),
        _m("chatting after the run", 50, role="user"),
    ]

    kept = without_runs(msgs, [RunWindow(started=20, ended=40)])

    assert [m.content for m in kept] == ["prepared in chat", "chatting after the run"]


def test_a_run_still_going_drops_everything_since_it_started() -> None:
    msgs = [_m("before", 5, role="user"), _m("step", 15), _m("step", 99)]

    kept = without_runs(msgs, [RunWindow(started=10, ended=None)])

    assert [m.content for m in kept] == ["before"]


async def _second_turns_history(seed: Any) -> str:
    """Send "first" over HTTP, let ``seed(spec, item_id, rid, conv)`` change the
    chat, send "second", and return what the second turn was handed: the path a
    person's message takes (`POST …/messages` → `ChatSendService`)."""
    from httpx import ASGITransport
    from specstar import QB
    from specstar.types import RevisionInfo

    from tests.api._client import AsyncClient
    from tests.api.conftest import register_rca_item
    from workspace_app.api import MessageDelta, RunDone, create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import Conversation, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    histories: list[list[dict[str, Any]]] = []

    class Recording:
        async def run(self, prompt: str, ctx: Any) -> AsyncIterator[Any]:
            histories.append(list(ctx.history or []))
            yield MessageDelta(text="reply")
            yield RunDone()

    spec = make_spec()
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=Recording()
    )
    iid = register_rca_item(spec)

    async def turns(n: int) -> None:
        async def wait() -> None:
            while len(histories) < n:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait(), 5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        first = await c.post(f"/a/rca/items/{iid}/messages", json={"content": "first"})
        assert first.status_code == 202
        await turns(1)
        conv_rm = spec.get_resource_manager(Conversation)
        (only,) = conv_rm.list_resources((QB["item_id"] == iid).build())
        info, conv = only.info, only.data
        assert isinstance(info, RevisionInfo) and isinstance(conv, Conversation)
        rid = info.resource_id
        seed(spec, iid, rid, conv)
        conv_rm.update(rid, conv)
        # to THIS chat: a chat with `run_id` is no longer the item's default
        second = await c.post(
            f"/a/rca/items/{iid}/chats/{rid}/messages", json={"content": "second"}
        )
        assert second.status_code == 202
        await turns(2)
    return " ".join(str(item) for item in histories[1])


async def test_a_chat_turn_sent_over_http_is_handed_no_run_message() -> None:
    def seed(spec: Any, item_id: str, rid: str, conv: Any) -> None:
        conv.messages.append(_m("a step's answer", conv.messages[-1].created_at, run_id="r1"))

    second = await _second_turns_history(seed)

    assert "first" in second and "reply" in second
    assert "a step's answer" not in second


async def test_a_chat_a_run_took_over_is_handed_none_of_the_runs_unmarked_messages() -> None:
    """#343 took over chats before messages were marked: the run's own start and
    end (its `WorkflowRun`, by `chat_id`) place them."""
    from workspace_app.workflow.run import RunStatus, WorkflowRun

    def seed(spec: Any, item_id: str, rid: str, conv: Any) -> None:
        at = conv.messages[-1].created_at + 1
        conv.messages.append(_m("a legacy step's answer", at))
        conv.run_id = "legacy-run"
        spec.get_resource_manager(WorkflowRun).create(
            WorkflowRun(
                item_id=item_id,
                captured_user="u",
                chat_id=rid,
                status=RunStatus.DONE,
                started=at,  # this run's window holds the legacy message only
                ended=at,
            )
        )

    second = await _second_turns_history(seed)

    assert "first" in second and "reply" in second
    assert "a legacy step's answer" not in second


async def test_a_run_of_another_chat_takes_nothing_from_this_one() -> None:
    """Runs are found by item, then by chat: a run on the item's other chat,
    whatever its window, places no message of this chat."""
    from workspace_app.workflow.run import RunStatus, WorkflowRun

    def seed(spec: Any, item_id: str, rid: str, conv: Any) -> None:
        at = conv.messages[-1].created_at + 1
        conv.messages.append(_m("this chat's own words", at, role="user"))
        conv.run_id = "legacy-run"
        spec.get_resource_manager(WorkflowRun).create(
            WorkflowRun(
                item_id=item_id,
                captured_user="u",
                chat_id="another-chat",
                status=RunStatus.DONE,
                started=0,
                ended=at + 1_000_000,
            )
        )

    second = await _second_turns_history(seed)

    assert "this chat's own words" in second


def test_a_message_with_no_time_cannot_be_placed_and_is_kept() -> None:
    msgs = [_m("from before timestamps existed", None, role="user")]

    assert without_runs(msgs, [RunWindow(started=0, ended=None)]) == msgs


def test_a_run_conversation_is_a_chat_only_if_a_person_spoke_in_it() -> None:
    """The chat list's rule (`chat_info.is_chat`): a conversation with no run is a
    chat; one with a run is a chat only if it holds a person's words — not a
    driver's (a goal round, a workflow step's prompt), not a run's. Whenever
    they spoke: under #343 a person could chat on after a run ended, and hiding
    that conversation would leave their words shown nowhere."""
    from workspace_app.api.chat_info import is_chat
    from workspace_app.resources import Conversation

    def conv(*msgs: Message, run_id: str | None = "r1") -> Conversation:
        return Conversation(item_id="i", run_id=run_id, messages=list(msgs))

    assert is_chat(conv(run_id=None))
    assert not is_chat(conv())
    assert not is_chat(conv(Message(role="user", content="round", driven_by="goal")))
    assert not is_chat(conv(_m("a step's prompt", 5, role="user", run_id="r1")))
    # a run conversation from before marks: its steps' unmarked answers
    assert not is_chat(conv(_m("a legacy step's answer", 5)))
    assert is_chat(conv(_m("asked after the run", 99, role="user")))


# ── review round 1 ──


def test_a_persons_own_words_inside_a_runs_window_are_kept() -> None:
    """Review R1: a window that never closed (a gate, a failed steer) cut every
    later word a person typed in that chat out of its history. A person's message
    was never a run's output."""
    msgs = [_m("my question", 50, role="user"), _m("legacy step", 60)]

    kept = without_runs(msgs, [RunWindow(started=10, ended=None)])

    assert [m.content for m in kept] == ["my question"]


async def test_a_run_that_settled_without_an_end_time_lets_the_chat_go_on() -> None:
    """Review R1: a steer that failed or was stopped settles `cancelled` with no
    `ended`, and its window read as open forever — the chat lost every turn after
    it. A settled run's window closes where it last did anything."""
    from workspace_app.workflow.run import RunStatus, WorkflowRun

    def seed(spec: Any, item_id: str, rid: str, conv: Any) -> None:
        conv.run_id = "legacy-run"
        spec.get_resource_manager(WorkflowRun).create(
            WorkflowRun(
                item_id=item_id,
                captured_user="u",
                chat_id=rid,
                status=RunStatus.CANCELLED,
                started=1,
                ended=None,
                progress_at=2,
            )
        )

    second = await _second_turns_history(seed)

    assert "first" in second and "reply" in second


async def test_a_run_waiting_at_a_gate_does_not_swallow_the_chat_after_it() -> None:
    """A gate may wait for days. Its window closes where the run last did anything
    (`progress_at`): the chat's own replies after that are the chat's."""
    from workspace_app.workflow.run import RunStatus, WorkflowRun

    def seed(spec: Any, item_id: str, rid: str, conv: Any) -> None:
        at = conv.messages[-1].created_at + 1
        # written by the run before it reached its gate, before messages were marked
        conv.messages.append(_m("a legacy step's answer", at + 1))
        conv.run_id = "legacy-run"
        spec.get_resource_manager(WorkflowRun).create(
            WorkflowRun(
                item_id=item_id,
                captured_user="u",
                chat_id=rid,
                status=RunStatus.AWAITING_HUMAN,
                started=at,
                ended=None,
                progress_at=at + 5,  # where it last acted: reaching the gate
            )
        )

    second = await _second_turns_history(seed)

    assert "a legacy step's answer" not in second  # inside the run, up to its gate
    assert "first" in second and "reply" in second
