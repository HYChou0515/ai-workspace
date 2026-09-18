"""A turn survives its pod (plan-graceful-shutdown P3).

`send` opens a durable claim beside the persisted question; the reply
persisting finishes it. A claim whose owner is gone — heartbeat stale, or let
go on purpose — is taken by a peer, which re-runs the recipe on its own engine
so the thread ends with the answer instead of the question.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

from fastapi import FastAPI
from specstar import SpecStar

from workspace_app.agent import AgentToolContext
from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone
from workspace_app.api.schemas import _MessageBody
from workspace_app.api.turn_activity import SpecstarTurnActivityStore
from workspace_app.api.turn_claims import TurnClaim
from workspace_app.api.turn_reclaim import ReclaimTick
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _Runner(ScriptedAgentRunner):
    """Answers with the text it was built with; counts its runs."""

    def __init__(self, reply: str = "the answer") -> None:
        super().__init__([MessageDelta(text=reply), RunDone()])
        self.runs = 0
        self.prompts: list[str] = []

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.runs += 1
        self.prompts.append(prompt)
        async for ev in super().run(prompt, ctx):
            yield ev


def _pod(spec: SpecStar, *, reply: str = "the answer") -> tuple[TestClient, _Runner]:
    """One API pod over the shared spec — the claim store, engines and send
    service `create_app` composes, with its own pod id. Its own reclaim
    sweeper is OFF: these tests drive the tick by hand, and a lifespan tick
    racing them took the orphan first (then the hand-driven tick saw a fresh
    heartbeat and, correctly, left it alone)."""
    runner = _Runner(reply)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        run_consumers=False,
        turn_reclaim_interval=None,
    )
    return TestClient(app), runner


def _item_with_chat(spec: SpecStar) -> tuple[str, str]:
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    rid = spec.get_resource_manager(Conversation).create(Conversation(item_id=item_id)).resource_id
    return item_id, rid


def _thread(spec: SpecStar, rid: str) -> list[tuple[str, str]]:
    conv = spec.get_resource_manager(Conversation).get(rid).data
    assert isinstance(conv, Conversation)
    return [(m.role, m.content) for m in conv.messages]


def _service(client: TestClient):
    return cast(FastAPI, client.app).state.chat_send


def _claims(client: TestClient):
    return cast(FastAPI, client.app).state.turn_claims


async def test_a_send_opens_a_claim_and_the_reply_finishes_it():
    spec = make_spec(default_user="u")
    client, runner = _pod(spec)
    item_id, rid = _item_with_chat(spec)
    conv = spec.get_resource_manager(Conversation).get(rid).data
    assert isinstance(conv, Conversation)
    with client:
        # Watch the claim appear while the turn is in flight: the runner is
        # scripted and fast, so look at the store from inside the turn instead.
        seen_during: list[str] = []
        original = runner.run

        async def spying(prompt, ctx):  # noqa: ANN001, ANN202
            seen_during.extend(r.claim.owner for r in _claims(client).list_open())
            async for ev in original(prompt, ctx):
                yield ev

        runner.run = spying  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
        await _service(client).send(
            item_id, rid, conv, item_id, _MessageBody(content="hi"), author="alice"
        )
    assert seen_during == [_claims(client).pod_id]  # owned by this pod while running
    assert _claims(client).list_open() == []  # finished with the reply
    assert _thread(spec, rid) == [("user", "hi"), ("assistant", "the answer")]


async def test_rerun_answers_the_stored_question_without_appending_it():
    """The peer's half: the question is already in the thread (pod A persisted
    it, then died). `rerun` builds the turn from that thread and the claim's
    recipe — exactly one user message, then the answer."""
    spec = make_spec(default_user="u")
    item_id, rid = _item_with_chat(spec)
    conv_rm = spec.get_resource_manager(Conversation)
    conv = conv_rm.get(rid).data
    assert isinstance(conv, Conversation)
    from workspace_app.resources import Message

    conv.messages.append(Message(role="user", content="hi", author="alice", created_at=1_000))
    conv_rm.update(rid, conv)
    client, runner = _pod(spec, reply="from the peer")
    with client:
        store = _claims(client)
        cid = store.open(
            TurnClaim(
                key=item_id,
                created_at=1_000,
                investigation_id=item_id,
                rid=rid,
                author="alice",
                body={"content": "hi"},
            )
        )
        (row,) = store.list_open()
        await _service(client).rerun(row)
        # The re-run's own turn is queued on this pod's engine; wait for it.
        for _ in range(100):
            if not store.list_open():
                break
            await asyncio.sleep(0.05)
    assert runner.runs == 1
    assert _thread(spec, rid) == [("user", "hi"), ("assistant", "from the peer")]
    assert store.list_open() == []
    assert cid  # the claim `open` wrote is the one `rerun` finished


# ── the reclaim decision, one tick at a time ─────────────────────────────


def _open_orphan(client: TestClient, spec: SpecStar, *, released: bool = False):
    """A claim as pod A would have left it: owned by A, question in the thread."""
    item_id, rid = _item_with_chat(spec)
    from workspace_app.resources import Message

    conv_rm = spec.get_resource_manager(Conversation)
    conv = conv_rm.get(rid).data
    assert isinstance(conv, Conversation)
    conv.messages.append(Message(role="user", content="hi", author="alice", created_at=1_000))
    conv_rm.update(rid, conv)
    store = _claims(client)
    store.open(
        TurnClaim(
            key=item_id,
            created_at=1_000,
            investigation_id=item_id,
            rid=rid,
            author="alice",
            body={"content": "hi"},
            owner="pod-a",
        )
    )
    if released:
        store.release(item_id)
    return item_id, rid


async def _settle(store, budget_s: float = 5.0) -> None:
    for _ in range(int(budget_s / 0.05)):
        if not store.list_open():
            return
        await asyncio.sleep(0.05)


async def test_a_released_claim_is_taken_and_run_at_once():
    spec = make_spec(default_user="u")
    client, runner = _pod(spec, reply="taken over")
    with client:
        item_id, rid = _open_orphan(client, spec, released=True)
        tick = ReclaimTick.of(cast(FastAPI, client.app))
        taken = await tick.run()
        await _settle(_claims(client))
    assert taken == [item_id]
    assert runner.runs == 1
    assert _thread(spec, rid)[-1] == ("assistant", "taken over")


async def test_a_claim_whose_owner_still_beats_is_left_alone():
    spec = make_spec(default_user="u")
    client, runner = _pod(spec)
    with client:
        item_id, _ = _open_orphan(client, spec)
        await SpecstarTurnActivityStore(spec).bump(item_id)  # pod A is alive
        taken = await ReclaimTick.of(cast(FastAPI, client.app)).run()
    assert taken == [] and runner.runs == 0
    assert len(_claims(client).list_open()) == 1


async def test_a_stale_claim_with_the_question_still_owed_is_taken():
    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec, reply="after the crash")
    with client:
        item_id, rid = _open_orphan(client, spec)
        await activity.bump(item_id)  # a beat at t=0: long stale by now
        tick = ReclaimTick.of(cast(FastAPI, client.app))
        taken = await tick.run()
        await _settle(_claims(client))
    assert taken == [item_id]
    assert runner.runs == 1
    assert _thread(spec, rid) == [("user", "hi"), ("assistant", "after the crash")]


async def test_a_stale_claim_whose_question_was_answered_is_dropped_not_rerun():
    """The turn ended (the reply is in the thread) but `finish` never landed —
    the pod died between the two writes. Re-running would answer twice."""
    spec = make_spec(default_user="u")
    client, runner = _pod(spec)
    with client:
        item_id, rid = _open_orphan(client, spec)
        from workspace_app.resources import Message

        conv_rm = spec.get_resource_manager(Conversation)
        conv = conv_rm.get(rid).data
        assert isinstance(conv, Conversation)
        conv.messages.append(Message(role="assistant", content="done", created_at=2_000))
        conv_rm.update(rid, conv)
        taken = await ReclaimTick.of(cast(FastAPI, client.app)).run()
    assert taken == [] and runner.runs == 0
    assert _claims(client).list_open() == []


async def test_taking_a_claim_advances_the_epoch_so_a_stalled_owner_stops():
    """A pod that only STALLED (its loop wedged, heartbeat stale) is not dead:
    when it recovers, its copy of the turn is still running. The taker bumps
    the shared epoch, and that copy's watcher cancels it — one answer."""
    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, _ = _pod(spec)
    with client:
        item_id, _ = _open_orphan(client, spec)
        await activity.bump(item_id)
        control = cast(FastAPI, client.app).state.turn_control
        before = await control.current(item_id)
        await ReclaimTick.of(cast(FastAPI, client.app)).run()
        await _settle(_claims(client))
        after = await control.current(item_id)
    assert after == before + 1


def _shared_backend(root):
    from specstar import BackendBinding, BackendConfig, ConnectionProfile

    return BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(root)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )


async def test_two_pods_on_one_store_hand_over_a_released_turn(tmp_path):
    """The production shape, in one process: two `create_app` pods, each with
    its own spec, over ONE disk backend (a spec cannot be composed twice — the
    job models refuse a second registration — which is also how production is
    shaped: one registry per process, one store between them). Pod A accepts
    a message and lets go of its claim (what its drain will do); pod B's tick
    runs the turn, and the thread ends with B's answer."""
    spec = make_spec(default_user="u", backend=_shared_backend(tmp_path))
    spec_b = make_spec(default_user="u", backend=_shared_backend(tmp_path))
    a, runner_a = _pod(spec, reply="A's answer")
    b, runner_b = _pod(spec_b, reply="B's answer")
    item_id, rid = _item_with_chat(spec)
    conv = spec.get_resource_manager(Conversation).get(rid).data
    assert isinstance(conv, Conversation)
    with a, b:
        # A persists the question and opens the claim, but its turn must not
        # get to answer: hold A's runner until B has taken over.
        gate = asyncio.Event()
        original = runner_a.run

        async def held(prompt, ctx):  # noqa: ANN001, ANN202
            await gate.wait()
            async for ev in original(prompt, ctx):
                yield ev

        runner_a.run = held  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
        send = asyncio.create_task(
            _service(a).send(item_id, rid, conv, item_id, _MessageBody(content="hi"), author="al")
        )
        for _ in range(100):
            if _claims(a).list_open():
                break
            await asyncio.sleep(0.02)
        _claims(a).release(item_id)  # A lets go (its drain would)
        taken = await ReclaimTick.of(cast(FastAPI, b.app)).run()
        await _settle(_claims(b))
        # A comes back. Its watcher polls the shared epoch every 0.5 s (#349),
        # so within a poll it cancels A's copy — before A's runner gets to
        # produce anything. (A pod that resumes and finishes inside that one
        # poll would answer twice; that window is #349's, not this one's.)
        engine_a = cast(FastAPI, a.app).state.turn_engines[0]
        for _ in range(60):
            turn = engine_a._ws_sessions[item_id].current_turn
            if turn is None or turn.done():
                break
            await asyncio.sleep(0.05)
        gate.set()
        await asyncio.wait_for(send, 5)
    assert taken == [item_id]
    assert runner_b.runs == 1
    assert runner_a.runs == 0  # cancelled by the epoch before it produced anything
    assert _thread(spec, rid) == [("user", "hi"), ("assistant", "B's answer")]


# ── the sweeper: the tick, on a timer, behind the fleet-wide lease ───────────


async def test_the_lifespan_sweeper_takes_over_a_released_turn_without_being_asked(tmp_path):
    """The production path end to end: pod A let go, pod B's LIFESPAN (not a
    test calling the tick) notices within its interval and answers."""
    from datetime import timedelta

    spec_a = make_spec(default_user="u", backend=_shared_backend(tmp_path))
    spec_b = make_spec(default_user="u", backend=_shared_backend(tmp_path))
    a, _ = _pod(spec_a)
    runner_b = _Runner("B answered on its own")
    b = TestClient(
        create_app(
            spec=spec_b,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner_b,
            run_consumers=False,
            turn_reclaim_interval=timedelta(seconds=0.1),
        )
    )
    with a:
        item_id, rid = _open_orphan(a, spec_a, released=True)
    with b:
        for _ in range(100):
            if _thread(spec_b, rid)[-1][0] == "assistant":
                break
            await asyncio.sleep(0.05)
    assert runner_b.runs == 1
    assert _thread(spec_b, rid) == [("user", "hi"), ("assistant", "B answered on its own")]
    assert item_id  # the claim named this key


async def test_no_sweeper_when_the_interval_is_none():
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        run_consumers=False,
        turn_reclaim_interval=None,
    )
    client = TestClient(app)
    with client:
        _, rid = _open_orphan(client, spec, released=True)
        await asyncio.sleep(0.3)
    assert _thread(spec, rid) == [("user", "hi")]  # nobody took it
    assert len(_claims(client).list_open()) == 1


# ── P4: the handover a draining pod makes ────────────────────────────────────


def test_a_pods_shutdown_hands_over_the_turn_it_could_not_finish(tmp_path):
    """SIGTERM path, end to end in one process: pod A is answering when its
    shutdown arrives; the turn does not finish inside the budget, so A lets go
    of the claim and cancels its copy WITHOUT persisting a partial reply or a
    cancel marker (that is Stop's meaning, not a rollout's); pod B's sweeper
    takes the claim and the thread ends with B's answer — clean, one answer,
    no "interrupted" between.

    Synchronous, and every call to A goes through its TestClient portal: the
    turn's tasks must live on the loop the lifespan shutdown drains, or the
    drain awaits tasks of another loop and never returns."""
    import threading
    import time
    from datetime import timedelta

    spec_a = make_spec(default_user="u", backend=_shared_backend(tmp_path))
    spec_b = make_spec(default_user="u", backend=_shared_backend(tmp_path))
    runner_a = _Runner("A's answer")
    a = TestClient(
        create_app(
            spec=spec_a,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner_a,
            run_consumers=False,
            turn_reclaim_interval=None,
            shutdown_budget=timedelta(seconds=0.3),
        )
    )
    runner_b = _Runner("B's answer")
    b = TestClient(
        create_app(
            spec=spec_b,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner_b,
            run_consumers=False,
            turn_reclaim_interval=timedelta(seconds=0.1),
        )
    )
    item_id, rid = _item_with_chat(spec_a)
    conv = spec_a.get_resource_manager(Conversation).get(rid).data
    assert isinstance(conv, Conversation)
    started = threading.Event()
    original = runner_a.run

    async def slow(prompt, ctx):  # noqa: ANN001, ANN202
        started.set()
        await asyncio.sleep(30)  # longer than any budget here
        async for ev in original(prompt, ctx):
            yield ev

    runner_a.run = slow  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]

    async def send_on_a() -> None:
        await _service(a).send(item_id, rid, conv, item_id, _MessageBody(content="hi"), author="al")

    with a:
        assert a.portal is not None
        a.portal.start_task_soon(send_on_a)
        assert started.wait(5), "A's turn never started"
        (row,) = _claims(a).list_open()
        assert not row.claim.released
    # Leaving the `with` ran A's lifespan shutdown on A's loop: the drain, the
    # release, the cancel.
    (row,) = _claims(a).list_open()
    assert row.claim.released, "A did not let go of the turn it could not finish"
    assert _thread(spec_a, rid) == [("user", "hi")]  # no partial, no marker
    with b:
        for _ in range(100):
            if _thread(spec_b, rid)[-1][0] == "assistant":
                break
            time.sleep(0.05)
    assert runner_b.runs == 1
    assert _thread(spec_b, rid) == [("user", "hi"), ("assistant", "B's answer")]
    assert _claims(b).list_open() == []
