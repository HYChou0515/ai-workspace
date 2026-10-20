"""PROBES (not fixes): can a user's AI answer someone else's question?

Each probe drives the real entrance and records what the model was actually
given, so the claim is checked against behaviour rather than read off the code.
"""

from __future__ import annotations

import asyncio
import time

from httpx import ASGITransport
from specstar import QB

from workspace_app.api import MessageDelta, RunDone, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient
from .conftest import register_rca_item


class _Recorder:
    """Records each turn's question and the history it ran with. The turn for
    Q1 is held open on `gate`, so Q2 is sent while Q1 is still being answered."""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate
        self.turns: list[tuple[str, str]] = []

    async def run(self, content, ctx):
        self.turns.append((content, repr(ctx.history)))
        if "Q1" in content:
            await self.gate.wait()
            yield MessageDelta(text="A1-answer-to-Q1")
        else:
            yield MessageDelta(text="A2-answer")
        yield RunDone()


def _conv(spec, iid: str) -> Conversation:
    for r in spec.get_resource_manager(Conversation).list_resources(QB.all()):
        if isinstance(r.data, Conversation) and r.data.item_id == iid:
            return r.data
    raise AssertionError("no conversation")


async def test_probe_1_a_queued_question_runs_without_the_answer_before_it():
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    gate = asyncio.Event()
    runner = _Recorder(gate)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,  # ty: ignore[invalid-argument-type]
        send_await_timeout=0.05,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post(f"/a/rca/items/{iid}/messages", json={"content": "Q1 from B"})
        r2 = await c.post(f"/a/rca/items/{iid}/messages", json={"content": "Q2 from A"})
    assert (r1.status_code, r2.status_code) == (202, 202)
    gate.set()
    for _ in range(200):
        if len(_conv(spec, iid).messages) >= 4:
            break
        await asyncio.sleep(0.02)
    thread = [(m.role, m.content) for m in _conv(spec, iid).messages]
    q2_turn = next(h for q, h in runner.turns if "Q2" in q)
    print("\nTHREAD:", thread)
    print("Q2 RAN WITH HISTORY:", q2_turn)
    await app.state.turn_engine.forget(iid)
    # The claim under test: Q2's history lacks A1 although A1 exists by then.
    assert "A1-answer-to-Q1" in str(thread)
    assert "Q1 from B" in q2_turn
    assert "A1-answer-to-Q1" not in q2_turn, "A1 WAS in Q2's history — probe 1 refuted"


async def test_control_1_sent_after_the_answer_the_history_has_it():
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    gate = asyncio.Event()
    gate.set()  # Q1 answers at once
    runner = _Recorder(gate)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,  # ty: ignore[invalid-argument-type]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post(f"/a/rca/items/{iid}/messages", json={"content": "Q1 from B"})
        await c.post(f"/a/rca/items/{iid}/messages", json={"content": "Q2 from A"})
    q2_turn = next(h for q, h in runner.turns if "Q2" in q)
    print("\nCONTROL Q2 HISTORY:", q2_turn)
    await app.state.turn_engine.forget(iid)
    assert "A1-answer-to-Q1" in q2_turn


def test_control_2_a_run_started_by_hand_leaves_the_default_chat_alone():
    from tests.api.test_headless_env import _executor_app

    _executor, item_id, spec, _runner, client = _executor_app(None, owner="owner-o")
    rm = spec.get_resource_manager(Conversation)
    default = rm.create(
        Conversation(item_id=item_id, messages=[], title="my own chat")
    ).resource_id
    with client:
        base = f"/a/playground/items/{item_id}"
        client.put(f"{base}/files/uploads/input.json", content='{"n": 1}')
        run_id = client.post(f"{base}/run").json()["run_id"]
        for _ in range(400):
            st = client.get(f"{base}/runs/{run_id}").json()["status"]
            if st in ("done", "error", "cancelled"):
                break
            time.sleep(0.02)
    mine = rm.get(default).data
    assert isinstance(mine, Conversation)
    print("\nCONTROL RUN STATUS:", st, "DEFAULT CHAT:", [(m.role, m.content) for m in mine.messages])
    assert st == "done" and mine.messages == []


def test_probe_2_an_event_started_run_answers_into_the_default_chat():
    """`event_dispatch` starts a run with NO chat_id (verified: event_dispatch.py
    `start_run(...)` passes none). Started that way here, through the app's own
    orchestrator, with the same arguments."""
    from tests.api.test_headless_env import _executor_app

    executor, item_id, spec, runner, client = _executor_app(None, owner="owner-o")
    rm = spec.get_resource_manager(Conversation)
    default = rm.create(
        Conversation(item_id=item_id, messages=[], title="my own chat")
    ).resource_id
    with client:
        base = f"/a/playground/items/{item_id}"
        client.put(f"{base}/files/uploads/input.json", content='{"n": 1}')
        orch = client.app.state.workflow_orchestrator  # ty: ignore[unresolved-attribute]
        run_id = client.portal.call(  # ty: ignore[unresolved-attribute]
            lambda: orch.start(
                slug="playground",
                item_id=item_id,
                profile="echo",
                captured_user="trigger-acting-user",
                workflow_id="",
                origin_trigger="entity:demo",
                trigger_depth=1,
            )
        )
        for _ in range(400):
            st = client.get(f"{base}/runs/{run_id}").json()["status"]
            if st in ("done", "error", "cancelled"):
                break
            time.sleep(0.02)
    mine = rm.get(default).data
    assert isinstance(mine, Conversation)
    print("\nRUN STATUS:", st)
    print("DEFAULT CHAT NOW:", [(m.role, m.content) for m in mine.messages])
    assert any(m.role == "assistant" for m in mine.messages), "probe 2 refuted: nothing landed"
    assert not any(m.role == "user" for m in mine.messages)
