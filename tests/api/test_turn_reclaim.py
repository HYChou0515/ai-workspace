"""An app-chat turn survives its pod (plan-graceful-shutdown P3; the KB
chat opens no claim and is not covered).

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
from workspace_app.api.request_env import IRequestEnv
from workspace_app.api.schemas import _MessageBody
from workspace_app.api.turn_activity import SpecstarTurnActivityStore
from workspace_app.api.turn_claims import SpecstarTurnClaimStore, TurnClaim
from workspace_app.api.turn_reclaim import RECLAIM_MAX_RERUNS, ReclaimTick
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


def _open_orphan(client: TestClient, spec: SpecStar, *, released: bool = False, reruns: int = 0):
    """A claim as pod A would have left it: opened by pod A's store (`open`
    stamps the opener, so the owner really is "pod-a", not this client's pod),
    the question in the thread."""
    item_id, rid = _item_with_chat(spec)
    from workspace_app.resources import Message

    conv_rm = spec.get_resource_manager(Conversation)
    conv = conv_rm.get(rid).data
    assert isinstance(conv, Conversation)
    conv.messages.append(Message(role="user", content="hi", author="alice", created_at=1_000))
    conv_rm.update(rid, conv)
    pod_a = SpecstarTurnClaimStore(spec, pod_id="pod-a")
    pod_a.open(
        TurnClaim(
            key=item_id,
            created_at=1_000,
            investigation_id=item_id,
            rid=rid,
            author="alice",
            body={"content": "hi"},
            reruns=reruns,
        )
    )
    if released:
        pod_a.release([item_id])
    return item_id, rid


def _append(spec: SpecStar, rid: str, role: str, content: str, created_at: int) -> None:
    from workspace_app.resources import Message

    conv_rm = spec.get_resource_manager(Conversation)
    conv = conv_rm.get(rid).data
    assert isinstance(conv, Conversation)
    conv.messages.append(Message(role=role, content=content, author="alice", created_at=created_at))
    conv_rm.update(rid, conv)


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


async def test_a_stale_claim_is_owed_whatever_the_thread_says_after_it():
    """The claim IS the ledger: it is finished when the reply persists and not
    before, so "does the thread still end on the question" is no evidence
    either way. A queued follow-up, the #624 notice, a peer's answer to a
    LATER question all sit after a question that is still unanswered — round
    1 of #815 found each of those shapes dropping the claim as "answered" and
    losing the reply for good, which is the one outcome this exists to end."""
    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec, reply="late, but here")
    with client:
        item_id, rid = _open_orphan(client, spec)
        _append(spec, rid, "notice", "older messages left out", 1_500)
        await activity.bump(item_id)  # stale
        taken = await ReclaimTick.of(cast(FastAPI, client.app)).run()
        await _settle(_claims(client))
    assert taken == [item_id] and runner.runs == 1
    assert _thread(spec, rid) == [
        ("user", "hi"),
        ("notice", "older messages left out"),
        ("assistant", "late, but here"),
    ]


async def test_two_stale_claims_on_one_key_are_taken_together_in_order():
    """A queued thread (Q1, Q2 — CLAUDE.md) whose pod died leaves TWO claims
    on one key. They are judged as one: the key's heartbeat is stale for
    both, both are taken on the same tick and re-run in the order they were
    asked, and the epoch is advanced ONCE, before either re-run. A second
    advance would cancel the first re-run through its own watcher — round 1
    found exactly that: A1 persisted as "interrupted", A2 answered."""
    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec, reply="answer")
    with client:
        item_id, rid = _open_orphan(client, spec)
        _append(spec, rid, "user", "the second question", 2_000)
        _claims(client).open(
            TurnClaim(
                key=item_id,
                created_at=2_000,
                investigation_id=item_id,
                rid=rid,
                author="alice",
                body={"content": "the second question"},
                owner="pod-a",
            )
        )
        await activity.bump(item_id)
        control = cast(FastAPI, client.app).state.turn_control
        before = await control.current(item_id)
        taken = await ReclaimTick.of(cast(FastAPI, client.app)).run()
        await _settle(_claims(client))
        after = await control.current(item_id)
    assert taken == [item_id]
    assert runner.runs == 2
    assert "hi" in runner.prompts[0] and "the second question" in runner.prompts[1]
    assert after == before + 1  # once, not once per claim
    assert _thread(spec, rid) == [
        ("user", "hi"),
        ("user", "the second question"),
        ("assistant", "answer"),
        ("assistant", "answer"),
    ]


async def test_taking_a_released_claim_leaves_the_epoch_alone():
    """A released claim's owner has already cancelled its copy — that is what
    releasing means (its drain does both, in that order). The advance is for
    a STALLED owner; on this path it would only reach bystanders — the
    taker's own running turn on the key, a third pod's — and cancel them as
    "interrupted"."""
    spec = make_spec(default_user="u")
    client, runner = _pod(spec)
    with client:
        item_id, _ = _open_orphan(client, spec, released=True)
        control = cast(FastAPI, client.app).state.turn_control
        before = await control.current(item_id)
        await ReclaimTick.of(cast(FastAPI, client.app)).run()
        await _settle(_claims(client))
        after = await control.current(item_id)
    assert after == before and runner.runs == 1


async def test_a_claim_rerun_too_often_ends_the_thread_with_an_error_instead():
    """A turn that cannot finish anywhere — its persist raises on every pod,
    say — must not be re-run every half-minute for ever, and the person must
    not wait for ever either: past `RECLAIM_MAX_RERUNS` the claim is finished
    and the thread gets an error ending that says so (#559 reads the last
    message, so this is what ends the waiting state)."""
    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec)
    with client:
        item_id, rid = _open_orphan(client, spec, reruns=RECLAIM_MAX_RERUNS)
        await activity.bump(item_id)
        taken = await ReclaimTick.of(cast(FastAPI, client.app)).run()
    assert taken == [] and runner.runs == 0
    assert _claims(client).list_open() == []
    role, text = _thread(spec, rid)[-1]
    assert role == "error" and str(RECLAIM_MAX_RERUNS + 1) in text


async def test_a_claim_finished_between_listing_and_taking_is_the_turn_ending(caplog):
    """The owner's persist lands between this tick's `list_open` and its
    `take`: the row is gone. That is the turn finishing, not a claim that
    "could not be judged" — no error, no re-run."""
    import dataclasses
    import logging

    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec)
    with client:
        item_id, _ = _open_orphan(client, spec)
        await activity.bump(item_id)
        (row,) = _claims(client).list_open()

        class _OwnerPersistsNow:
            async def alive(self, key: str) -> bool:
                _claims(client).finish(row.id)  # the owner's reply lands just now
                return False

        tick = dataclasses.replace(
            ReclaimTick.of(cast(FastAPI, client.app)), activity=_OwnerPersistsNow()
        )  # type: ignore[arg-type]
        with caplog.at_level(logging.ERROR):
            taken = await tick.run()
    assert taken == [] and runner.runs == 0
    assert "could not be judged" not in caplog.text


async def test_a_claim_beats_while_its_turn_is_still_being_prepared():
    """The claim is opened at persist; the turn's own heartbeat starts when
    the turn RUNS. The preparation in between — a cold sandbox wake,
    compaction on a slow model — can outlast `TURN_STALE_AFTER_MS`, and a
    claim with no beat behind it reads as a dead owner: a peer takes it,
    cancels this copy through the epoch and re-runs — twice the cost for one
    answer, on every slow preparation. So the preparation beats too."""
    spec = make_spec(default_user="u")
    client, _ = _pod(spec)
    item_id, rid = _item_with_chat(spec)
    conv = spec.get_resource_manager(Conversation).get(rid).data
    assert isinstance(conv, Conversation)
    with client:
        svc = _service(client)
        gate = asyncio.Event()
        real_compact = svc.compact

        async def slow_compact(*a, **kw):  # noqa: ANN002, ANN003, ANN202
            await gate.wait()
            return await real_compact(*a, **kw)

        svc.compact = slow_compact  # the instance attribute shadows the method
        send = asyncio.create_task(
            svc.send(item_id, rid, conv, item_id, _MessageBody(content="hi"), author="alice")
        )
        for _ in range(100):
            if _claims(client).list_open():
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)  # the first beat lands on the next loop turn
        alive = await cast(FastAPI, client.app).state.turn_activity.alive(item_id)
        gate.set()
        await asyncio.wait_for(send, 5)
    assert alive, "no heartbeat behind the claim while its turn was being prepared"


async def test_a_rerun_takes_its_history_from_before_the_claimed_question():
    """The thread a re-run reads may have grown past the question — a queued
    follow-up, the #624 notice. History is everything BEFORE the claimed
    message, found by its timestamp and text rather than assumed to be last,
    so the model is not handed the question twice (round 1)."""
    spec = make_spec(default_user="u")
    item_id, rid = _item_with_chat(spec)
    _append(spec, rid, "user", "the earlier question", 500)
    _append(spec, rid, "assistant", "the earlier answer", 600)
    _append(spec, rid, "user", "the claimed question", 1_000)
    _append(spec, rid, "notice", "older messages left out", 1_500)
    client, runner = _pod(spec, reply="from the peer")
    histories: list[str] = []
    original = runner.run

    async def spying(prompt, ctx):  # noqa: ANN001, ANN202
        histories.append(" | ".join(h["content"] for h in ctx.history))
        async for ev in original(prompt, ctx):
            yield ev

    runner.run = spying  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    with client:
        store = _claims(client)
        store.open(
            TurnClaim(
                key=item_id,
                created_at=1_000,
                investigation_id=item_id,
                rid=rid,
                author="alice",
                body={"content": "the claimed question"},
            )
        )
        (row,) = store.list_open()
        await _service(client).rerun(row)
        await _settle(store)
    (history,) = histories
    assert "the earlier question" in history and "the earlier answer" in history
    assert "the claimed question" not in history
    assert "the claimed question" in runner.prompts[0]


async def test_a_rerun_runs_on_the_headless_env_not_on_a_stored_cookie():
    """#714's request env is composed for ONE turn and never written back —
    it is the caller's own cookie, a header their gateway stamped on. The
    claim carries none of it (round 1 found it persisted, plaintext, for the
    turn's whole life). A re-run is a turn nobody pressed send for, so it
    asks the seam what such a turn gets — the goal driver's answer."""

    class _Seam(IRequestEnv):
        async def env_for(self, request, *, user_id: str, item_id: str) -> dict[str, str]:  # noqa: ANN001
            return {"COOKIE": "the-secret"}

        async def env_without_request(self, *, user_id: str, item_id: str) -> dict[str, str]:
            return {"HEADLESS_FOR": user_id}

    assert "caller_env" not in TurnClaim.__struct_fields__
    spec = make_spec(default_user="u")
    item_id, rid = _item_with_chat(spec)
    _append(spec, rid, "user", "hi", 1_000)
    runner = _Runner("from the peer")
    envs: list[dict[str, str]] = []
    original = runner.run

    async def spying(prompt, ctx):  # noqa: ANN001, ANN202
        envs.append(dict(ctx.user_env))
        async for ev in original(prompt, ctx):
            yield ev

    runner.run = spying  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner,
            run_consumers=False,
            turn_reclaim_interval=None,
            request_env=_Seam(),
        )
    )
    with client:
        store = _claims(client)
        store.open(
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
        await _settle(store)
    assert envs == [{"HEADLESS_FOR": "alice"}]


async def test_a_key_a_peer_is_taking_is_left_whole():
    """Two ticks that overlap at a lease-window boundary list the same stale
    key; the first `take` that loses its CAS means a peer is on this key, and
    the whole key is left to it. Splitting it — B takes Q1, C takes Q2 — had
    both advance the epoch, and whichever landed second cancelled the other's
    legitimate re-run through Stop's path: partial, "interrupted", claim
    finished, Q1 never answered (round 2)."""
    import dataclasses

    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec)
    with client:
        item_id, rid = _open_orphan(client, spec)
        _append(spec, rid, "user", "the second question", 2_000)
        _claims(client).open(
            TurnClaim(
                key=item_id,
                created_at=2_000,
                investigation_id=item_id,
                rid=rid,
                author="alice",
                body={"content": "the second question"},
                owner="pod-a",
            )
        )
        await activity.bump(item_id)
        peer = SpecstarTurnClaimStore(spec, pod_id="peer")
        control = cast(FastAPI, client.app).state.turn_control
        before = await control.current(item_id)

        class _PeerTakesFirst:
            async def alive(self, key: str) -> bool:
                first = min(peer.list_open(), key=lambda r: r.claim.created_at)
                peer.take(first)  # the peer's tick got there between our list and our take
                return False

        tick = dataclasses.replace(
            ReclaimTick.of(cast(FastAPI, client.app)),
            activity=_PeerTakesFirst(),  # type: ignore[arg-type]
        )
        taken = await tick.run()
        after = await control.current(item_id)
    assert taken == [] and runner.runs == 0
    assert after == before  # the peer's advance is the only one this key gets
    # Q2 untouched (still as `open` left it, this store's own id, no rerun):
    # the peer's to take on its pass.
    rows = {r.claim.created_at: r.claim for r in _claims(client).list_open()}
    assert rows[1_000].owner == "peer"
    assert rows[2_000].owner == _claims(client).pod_id and rows[2_000].reruns == 0  # opened here


async def test_a_take_that_raises_skips_that_claim_and_still_runs_the_rest(caplog):
    """A transient store error on one claim's `take` is that claim's problem:
    the rest of the key is still taken, advanced ONCE and re-run. Aborting
    the key after the first take had that claim owned by this pod with no
    beat and nothing running — re-taken 30 s later as stale, and after two
    such hiccups given up on (round 2)."""
    import dataclasses
    import logging

    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec)
    with client:
        item_id, rid = _open_orphan(client, spec)
        _append(spec, rid, "user", "the second question", 2_000)
        store = _claims(client)
        store.open(
            TurnClaim(
                key=item_id,
                created_at=2_000,
                investigation_id=item_id,
                rid=rid,
                author="alice",
                body={"content": "the second question"},
                owner="pod-a",
            )
        )
        await activity.bump(item_id)
        control = cast(FastAPI, client.app).state.turn_control
        before = await control.current(item_id)

        class _FlakyOnTheSecond:
            def __getattr__(self, name):  # noqa: ANN001, ANN204 — the real store, but for `take`
                return getattr(store, name)

            def take(self, row):  # noqa: ANN001, ANN202
                if row.claim.created_at == 2_000:
                    raise RuntimeError("store hiccup")
                return store.take(row)

        tick = dataclasses.replace(
            ReclaimTick.of(cast(FastAPI, client.app)),
            claims=_FlakyOnTheSecond(),  # type: ignore[arg-type]
        )
        with caplog.at_level(logging.ERROR):
            taken = await tick.run()
        await _settle(store)
        after = await control.current(item_id)
    assert taken == [item_id] and runner.runs == 1
    assert after == before + 1
    assert "hi" in runner.prompts[0]
    (left,) = store.list_open()
    assert left.claim.created_at == 2_000 and left.claim.reruns == 0  # next tick's


async def test_only_the_tick_that_takes_a_spent_claim_writes_its_ending():
    """Two overlapping ticks both see a claim past `RECLAIM_MAX_RERUNS`. The
    ending is written by whoever TAKES it (a CAS), so the thread gets one
    error message, not one per tick (round 2)."""
    import dataclasses

    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec)
    with client:
        item_id, rid = _open_orphan(client, spec, reruns=RECLAIM_MAX_RERUNS)
        await activity.bump(item_id)
        peer = SpecstarTurnClaimStore(spec, pod_id="peer")

        class _PeerTakesFirst:
            async def alive(self, key: str) -> bool:
                (row,) = peer.list_open()
                peer.take(row)
                return False

        tick = dataclasses.replace(
            ReclaimTick.of(cast(FastAPI, client.app)),
            activity=_PeerTakesFirst(),  # type: ignore[arg-type]
        )
        taken = await tick.run()
    assert taken == [] and runner.runs == 0
    assert _thread(spec, rid) == [("user", "hi")]  # the peer's ending, not ours as well
    (row,) = _claims(client).list_open()
    assert row.claim.owner == "peer"


async def test_a_send_still_preparing_at_the_deadline_is_handed_over_and_declined():
    """The drain's third shape, after running and queued: a send whose claim
    is open but whose turn does not exist yet (compaction, a cold sandbox
    wake). It is handed over like the others, and its token is marked so
    the turn never starts here — the preparation that finishes after the
    drain enqueues, the worker declines, and the declined copy persists
    nothing (the claim is no longer this pod's). Round 2 found it neither
    handed over nor stopped: the turn ran on the drained engine and, at
    exit, persisted a partial with its claim finished (round 2)."""
    spec = make_spec(default_user="u")
    client, runner = _pod(spec)
    item_id, rid = _item_with_chat(spec)
    conv = spec.get_resource_manager(Conversation).get(rid).data
    assert isinstance(conv, Conversation)
    with client:
        svc = _service(client)
        store = _claims(client)
        engine = cast(FastAPI, client.app).state.turn_engines[0]
        gate = asyncio.Event()
        real_compact = svc.compact

        async def slow_compact(*a, **kw):  # noqa: ANN002, ANN003, ANN202
            await gate.wait()
            return await real_compact(*a, **kw)

        svc.compact = slow_compact
        seen: list[str] = []

        async def collect() -> None:
            async for ev in engine.subscribe(item_id):
                seen.append(type(ev).__name__)

        collector = asyncio.create_task(collect())
        send = asyncio.create_task(
            svc.send(item_id, rid, conv, item_id, _MessageBody(content="hi"), author="alice")
        )
        for _ in range(100):
            if store.list_open():
                break
            await asyncio.sleep(0.02)

        async def handover(keys: list[str], not_after: float) -> None:
            await asyncio.to_thread(store.release, keys, not_after=not_after)

        await engine.aclose(timeout=0.3, handover=handover)
        (row,) = store.list_open()
        assert row.claim.released, "the preparing send was not handed over"
        gate.set()  # the preparation finishes on the drained engine
        await asyncio.wait_for(send, 5)
        await asyncio.sleep(0.05)
        collector.cancel()
    assert runner.runs == 0  # declined, never run here
    assert _thread(spec, rid) == [("user", "hi")]  # no partial, no marker
    (row,) = _claims(client).list_open()
    assert row.claim.released  # still the peer's to answer
    assert "RunCancelled" not in seen, seen  # and the viewers were not told "cancelled"


async def test_a_rerun_beats_while_resolving_the_headless_env():
    """`env_without_request` is a deploy-owned policy that may take its time
    (minting a credential); the claim is the taker's from `take` on, and
    without a beat behind it the next tick would read a stale key and take
    it AGAIN — a second copy (round 2). So the resolution runs inside the
    preparation window, under its beat."""

    class _SlowSeam(IRequestEnv):
        def __init__(self) -> None:
            self.gate = asyncio.Event()

        async def env_for(self, request, *, user_id: str, item_id: str) -> dict[str, str]:  # noqa: ANN001
            return {}

        async def env_without_request(self, *, user_id: str, item_id: str) -> dict[str, str]:
            await self.gate.wait()
            return {}

    seam = _SlowSeam()
    spec = make_spec(default_user="u")
    item_id, rid = _item_with_chat(spec)
    _append(spec, rid, "user", "hi", 1_000)
    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=_Runner(),
            run_consumers=False,
            turn_reclaim_interval=None,
            request_env=seam,
        )
    )
    with client:
        store = _claims(client)
        store.open(
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
        rerun = asyncio.create_task(_service(client).rerun(row))
        await asyncio.sleep(0.1)  # inside the seam's await; the first beat has landed
        alive = await cast(FastAPI, client.app).state.turn_activity.alive(item_id)
        seam.gate.set()
        await asyncio.wait_for(rerun, 5)
        await _settle(store)
    assert alive, "no heartbeat behind the claim while the re-run resolved its env"


async def test_rerun_returns_once_the_turn_is_queued_not_answered():
    """The tick runs every claim it took in turn; a `rerun` that waited on the
    reply (up to `send_await_timeout`, as the POST does) would start the
    Nth orphan N detach-timeouts late. Round 1 made it return at once and
    said it was pinned; nothing reddened when that was mutated (round 2)."""
    spec = make_spec(default_user="u")
    item_id, rid = _item_with_chat(spec)
    _append(spec, rid, "user", "hi", 1_000)
    runner = _Runner()
    gate = asyncio.Event()
    original = runner.run

    async def held(prompt, ctx):  # noqa: ANN001, ANN202
        await gate.wait()
        async for ev in original(prompt, ctx):
            yield ev

    runner.run = held  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner,
            run_consumers=False,
            turn_reclaim_interval=None,
            send_await_timeout=3.0,  # what a rerun that waited would wait
        )
    )
    with client:
        store = _claims(client)
        store.open(
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
        returned_with_the_gate_shut = False
        try:
            await asyncio.wait_for(_service(client).rerun(row), 1.0)
            returned_with_the_gate_shut = runner.runs == 0
        except TimeoutError:
            pass  # the verdict is below; a hang here would be a hang of the drain
        finally:
            # A structural exit: the turn's tasks live on THIS loop and the
            # lifespan drain on the client's, so leaving the `with` while the
            # turn is live would wait on a foreign loop for ever.
            gate.set()
            await _settle(store)
    assert returned_with_the_gate_shut, "rerun waited on the reply"
    assert _thread(spec, rid) == [("user", "hi"), ("assistant", "the answer")]


async def test_an_abandon_whose_finish_fails_writes_one_ending_not_one_per_tick():
    """Giving up appends the error ending and finishes the claim; a `finish`
    the store refuses leaves the row for the next tick, which must not append
    the same ending again (round 2: three ticks, three "could not finish"
    messages). The ending is idempotent: written only if the thread does not
    already end on it."""
    import dataclasses

    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec)
    with client:
        item_id, rid = _open_orphan(client, spec, reruns=RECLAIM_MAX_RERUNS)
        await activity.bump(item_id)
        store = _claims(client)

        class _FinishRefused:
            def __getattr__(self, name):  # noqa: ANN001, ANN204
                return getattr(store, name)

            def finish(self, cid: str) -> None:
                raise RuntimeError("store refused")

        tick = dataclasses.replace(
            ReclaimTick.of(cast(FastAPI, client.app)),
            claims=_FinishRefused(),  # type: ignore[arg-type]
        )
        # The tick's own ChatSendService still finishes through the real store;
        # point it at the refusing one too, so the ending is written but the
        # row stays.
        tick.chat_send._turn_claims = _FinishRefused()  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
        await tick.run()
        await tick.run()
        await tick.run()
        tick.chat_send._turn_claims = store  # type: ignore[assignment]
    assert runner.runs == 0
    endings = [m for m in _thread(spec, rid) if m[0] == "error"]
    assert len(endings) == 1, endings


async def test_giving_up_still_advances_the_epoch_on_a_stalled_owner():
    """A claim past its bound on a STALLED (not dead) owner: the give-up ends
    the thread, and the epoch is advanced all the same so the owner's copy —
    still running, still burning the model — cancels itself instead of
    finishing into a claim that is gone (round 2)."""
    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, _ = _pod(spec)
    with client:
        item_id, _ = _open_orphan(client, spec, reruns=RECLAIM_MAX_RERUNS)
        await activity.bump(item_id)
        control = cast(FastAPI, client.app).state.turn_control
        before = await control.current(item_id)
        await ReclaimTick.of(cast(FastAPI, client.app)).run()
        after = await control.current(item_id)
    assert after == before + 1


async def test_a_stale_key_takes_its_released_and_unreleased_claims_together():
    """One key, Q1's claim owned by a pod that died (stale, not released) and
    Q2's released by a pod that drained. Taking only the released one left
    Q1 waiting until Q2's re-run ended AND its beat went stale — answered
    after Q2, half a minute late (round 2). A stale key is taken whole."""
    spec = make_spec(default_user="u")
    activity = SpecstarTurnActivityStore(spec, now_ms=lambda: 0)
    client, runner = _pod(spec, reply="answer")
    with client:
        item_id, rid = _open_orphan(client, spec)  # Q1: pod-a, dead
        _append(spec, rid, "user", "the second question", 2_000)
        drained = SpecstarTurnClaimStore(spec, pod_id="pod-b")
        drained.open(
            TurnClaim(
                key=item_id,
                created_at=2_000,
                investigation_id=item_id,
                rid=rid,
                author="alice",
                body={"content": "the second question"},
            )
        )
        drained.release([item_id])  # Q2: pod-b let go
        await activity.bump(item_id)
        taken = await ReclaimTick.of(cast(FastAPI, client.app)).run()
        await _settle(_claims(client))
    assert taken == [item_id] and runner.runs == 2
    assert "hi" in runner.prompts[0] and "the second question" in runner.prompts[1]
    assert _thread(spec, rid) == [
        ("user", "hi"),
        ("user", "the second question"),
        ("assistant", "answer"),
        ("assistant", "answer"),
    ]


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


async def test_two_pods_on_one_store_a_stalled_owner_is_taken_over_and_writes_nothing(
    tmp_path,
):
    """The production shape, in one process: two `create_app` pods, each with
    its own spec, over ONE disk backend (a spec cannot be composed twice — the
    job models refuse a second registration — which is also how production is
    shaped: one registry per process, one store between them). Pod A accepts
    a message and STALLS mid-turn (its heartbeat reads stale to B — here by
    B's clock running ahead, since a wedged loop cannot be staged); pod B's
    tick takes the claim, advances the epoch and runs the turn. A's watcher
    then cancels A's copy, which persists NOTHING — no partial, no
    "interrupted" marker — and broadcasts nothing either: the thread ends
    with B's answer and A's viewers never saw a cancel."""
    import dataclasses

    from workspace_app.api.timeutil import now_ms

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
        # What A's viewers would see: the superseded copy must not broadcast
        # its cancel — a `run_cancelled` on the bus reaches the live pod's
        # viewers as a "cancelled" banner seconds before B's re-run starts
        # streaming (round 1).
        engine_a = cast(FastAPI, a.app).state.turn_engines[0]
        seen_on_a: list[str] = []

        async def collect() -> None:
            async for ev in engine_a.subscribe(item_id):
                seen_on_a.append(type(ev).__name__)

        collector = asyncio.create_task(collect())
        send = asyncio.create_task(
            _service(a).send(item_id, rid, conv, item_id, _MessageBody(content="hi"), author="al")
        )
        for _ in range(100):
            if _claims(a).list_open():
                break
            await asyncio.sleep(0.02)
        # B's clock is a minute ahead: A's beats, real as they are, read stale.
        ahead = SpecstarTurnActivityStore(spec_b, now_ms=lambda: now_ms() + 60_000)
        tick = dataclasses.replace(ReclaimTick.of(cast(FastAPI, b.app)), activity=ahead)
        taken = await tick.run()
        await _settle(_claims(b))
        # A's watcher polls the shared epoch every 0.5 s (#349), so within a
        # poll it cancels A's copy — before A's runner gets to produce
        # anything. (A pod that resumes and finishes inside that one poll
        # would answer twice; that window is #349's, not this one's.)
        for _ in range(60):
            turn = engine_a._ws_sessions[item_id].current_turn
            if turn is None or turn.done():
                break
            await asyncio.sleep(0.05)
        gate.set()
        await asyncio.wait_for(send, 5)
        collector.cancel()
    assert taken == [item_id]
    assert runner_b.runs == 1
    assert runner_a.runs == 0  # cancelled by the epoch before it produced anything
    assert _thread(spec, rid) == [("user", "hi"), ("assistant", "B's answer")]
    assert "RunCancelled" not in seen_on_a, seen_on_a


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


def _pod_a(tmp_path, runner: _Runner, *, budget_s: float) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user="u", backend=_shared_backend(tmp_path))
    from datetime import timedelta

    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner,
            run_consumers=False,
            turn_reclaim_interval=None,
            shutdown_budget=timedelta(seconds=budget_s),
            # The POSTs detach at once: a POST still waiting on its reply keeps
            # its preparation heartbeat alive, and that task sat in the drain's
            # first snapshot and waited the queued turn out by accident — the
            # re-snapshot below was not what these tests were exercising.
            send_await_timeout=0.05,
        )
    )
    return client, spec


def _paced(runner: _Runner, delays: dict[str, float]) -> None:
    """Make `runner` sleep before answering a prompt, by the text it contains."""
    original = runner.run

    async def paced(prompt, ctx):  # noqa: ANN001, ANN202
        await asyncio.sleep(next((d for text, d in delays.items() if text in prompt), 0))
        async for ev in original(prompt, ctx):
            yield ev

    runner.run = paced  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]


def _send_via_portal(client: TestClient, spec: SpecStar, item_id: str, rid: str, text: str) -> None:
    conv = spec.get_resource_manager(Conversation).get(rid).data
    assert isinstance(conv, Conversation)

    async def go() -> None:
        await _service(client).send(
            item_id, rid, conv, item_id, _MessageBody(content=text), author="al"
        )

    assert client.portal is not None
    client.portal.start_task_soon(go)


def _wait_until(pred, what: str, budget_s: float = 5.0) -> None:  # noqa: ANN001
    import time

    for _ in range(int(budget_s / 0.02)):
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(what)


def test_a_pods_shutdown_waits_for_a_turn_that_started_during_the_drain(tmp_path):
    """Q1 is running and Q2 queued when the shutdown arrives; Q1 finishes
    inside the budget and the worker starts Q2 — a turn that did not exist
    when the drain began. With budget left it is waited for like any other:
    the thread leaves with both answers and no claim behind. (Round 1: the
    drain waited on a snapshot, so Q2 was cancelled the moment Q1 ended.)"""
    runner = _Runner("answer")
    _paced(runner, {"q1": 0.2, "q2": 0.3})
    a, spec = _pod_a(tmp_path, runner, budget_s=3)
    item_id, rid = _item_with_chat(spec)
    with a:
        _send_via_portal(a, spec, item_id, rid, "q1")
        _wait_until(lambda: _thread(spec, rid) == [("user", "q1")], "q1 not persisted")
        _send_via_portal(a, spec, item_id, rid, "q2")
        _wait_until(lambda: len(_claims(a).list_open()) == 2, "q2 not claimed")
        _wait_until(
            lambda: (
                not cast(FastAPI, a.app).state.turn_engines[0]._ws_sessions[item_id].pending_turns
            ),
            "the POSTs did not detach",
        )
    assert _thread(spec, rid) == [
        ("user", "q1"),
        ("user", "q2"),
        ("assistant", "answer"),
        ("assistant", "answer"),
    ]
    assert _claims(a).list_open() == []


def test_a_pods_shutdown_hands_over_the_queued_turn_that_started_during_the_drain(tmp_path):
    """Same start; Q2 cannot finish inside what is left of the budget. It is
    handed over like a turn that was running from the start — its claim
    released, its copy cancelled WITHOUT a partial or a marker — instead of
    being cancelled through Stop's path with its claim finished as this
    pod's (round 1: `[q1, q2, A1, "interrupted"]`, nothing left to take)."""
    runner = _Runner("answer")
    _paced(runner, {"q1": 0.2, "q2": 30})
    a, spec = _pod_a(tmp_path, runner, budget_s=1)
    item_id, rid = _item_with_chat(spec)
    with a:
        _send_via_portal(a, spec, item_id, rid, "q1")
        _wait_until(lambda: _thread(spec, rid) == [("user", "q1")], "q1 not persisted")
        _send_via_portal(a, spec, item_id, rid, "q2")
        _wait_until(lambda: len(_claims(a).list_open()) == 2, "q2 not claimed")
    assert _thread(spec, rid) == [("user", "q1"), ("user", "q2"), ("assistant", "answer")]
    (row,) = _claims(a).list_open()
    assert row.claim.released and row.claim.body["content"] == "q2"


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
