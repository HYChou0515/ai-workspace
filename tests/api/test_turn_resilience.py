"""The turn worker and its live subscribers must survive their own failures.

Two ways a turn used to vanish without anybody learning of it:

* ``on_complete`` (which PERSISTS the turn) ran unguarded inside ``_run_turn``'s
  ``finally``, unlike the ``on_turn_end`` flush right beside it. A raise there —
  the chat deleted mid-turn, a store error — escaped ``_run_turn``; ``_worker``
  suppresses only ``CancelledError``, so the worker died before resolving the
  waiting POST and before ``task_done()``. The turn was lost, the POST hung its
  full detach window, and every LATER message on that conversation queued behind
  a worker that no longer existed.

* ``forget`` popped the session while its SSE subscribers kept their queues. The
  in-flight turn published into the OLD session object, so those viewers received
  nothing ever again — while the heartbeat kept flowing, so the stream looked
  healthy and the client never reconnected.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, cast

from workspace_app.agent import AgentToolContext
from workspace_app.api import MessageDelta, RunDone
from workspace_app.api.turns import ChatTurnEngine


class _Runner:
    """Emits one delta then finishes."""

    async def run(self, content, ctx):  # noqa: ANN001, ANN201
        yield MessageDelta(text=content)
        yield RunDone()


async def test_a_failing_persist_does_not_kill_the_worker():
    """The next message still runs. Before, the worker died on the first raise and
    every later turn on that conversation queued forever behind a dead worker."""
    seen: list[str] = []
    second_done = asyncio.Event()

    def on_complete(msgs):  # noqa: ANN001, ANN202
        seen.append(msgs[0].content)
        if len(seen) == 1:
            raise RuntimeError("store exploded")  # persisting the FIRST turn fails
        second_done.set()

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    engine.enqueue("inv", "a", AgentToolContext(), on_complete=on_complete)
    engine.enqueue("inv", "b", AgentToolContext(), on_complete=on_complete)

    await asyncio.wait_for(second_done.wait(), 3)
    assert seen == ["a", "b"]
    await engine.forget("inv")


async def test_a_failing_persist_still_wakes_the_waiting_post():
    """`enqueue` hands back a future the POST awaits; a persist failure must not
    strand it (the request would hang to its detach timeout, then 202)."""
    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]

    def on_complete(_msgs):  # noqa: ANN001, ANN202
        raise RuntimeError("store exploded")

    fut = engine.enqueue("inv2", "a", AgentToolContext(), on_complete=on_complete)
    await asyncio.wait_for(fut, 3)
    await engine.forget("inv2")


async def test_a_failing_persist_tells_the_subscribers():
    """A turn that could not be saved must reach the viewer as a terminal error.
    Silently dropping it is the worst case: the reply streamed live, then vanished
    on the next reload with no explanation."""
    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    frames: list[str] = []

    async def read() -> None:
        async for frame in engine.subscribe_sse("inv3", heartbeat_interval=0.05):
            frames.append(frame)

    reader = asyncio.create_task(read())
    await asyncio.sleep(0.05)  # let the subscriber attach

    def on_complete(_msgs):  # noqa: ANN001, ANN202
        raise RuntimeError("store exploded")

    await asyncio.wait_for(
        engine.enqueue("inv3", "a", AgentToolContext(), on_complete=on_complete), 3
    )
    await asyncio.sleep(0.05)
    reader.cancel()

    assert any("error" in f for f in frames), frames


async def test_forget_ends_the_live_streams_instead_of_orphaning_them():
    """`forget` must close the SSE responses it is dropping the session for.

    Otherwise the viewer keeps an open, heartbeating, permanently deaf connection
    — the client cannot tell that apart from a quiet chat, so it never reconnects.
    """
    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    ended = asyncio.Event()

    async def read() -> None:
        async for _frame in engine.subscribe_sse("inv4", heartbeat_interval=5.0):
            pass
        ended.set()  # the generator RETURNED — the SSE response is over

    reader = asyncio.create_task(read())
    await asyncio.sleep(0.05)  # let the subscriber attach

    await engine.forget("inv4")

    await asyncio.wait_for(ended.wait(), 3)
    reader.cancel()


async def test_the_worker_survives_a_turn_that_escapes_every_inner_guard():
    """Defence in depth for the worker's own contract.

    ``_run_turn`` guards its persist and its flush, so today nothing should
    escape it — but "the worker outlives its turns" must hold structurally, not
    by trusting one function to stay total. A later edit that reintroduces an
    escape has to not take the conversation's worker down with it.
    """
    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]

    async def _boom(*_a, **_k) -> None:  # noqa: ANN002, ANN003
        raise RuntimeError("escaped every inner guard")

    engine._run_turn = _boom  # ty: ignore[invalid-assignment]

    fut = engine.enqueue("inv5", "a", AgentToolContext(), on_complete=lambda _m: None)
    await asyncio.wait_for(fut, 3)  # the POST is still woken

    # And the worker is alive: a second message still gets picked up.
    fut2 = engine.enqueue("inv5", "b", AgentToolContext(), on_complete=lambda _m: None)
    await asyncio.wait_for(fut2, 3)
    await engine.forget("inv5")


async def test_an_abandoned_post_future_is_left_alone():
    """The POST can give up first (its detach timeout) and cancel the future it
    was awaiting. Resolving it again would raise InvalidStateError inside the
    worker — taking down the worker over a request nobody is waiting for."""
    started = asyncio.Event()
    release = asyncio.Event()

    class _Slow:
        async def run(self, content, ctx):  # noqa: ANN001, ANN201
            started.set()
            await release.wait()
            yield RunDone()

    engine = ChatTurnEngine(_Slow())  # ty: ignore[invalid-argument-type]
    fut = engine.enqueue("inv6", "a", AgentToolContext(), on_complete=lambda _m: None)
    await asyncio.wait_for(started.wait(), 3)
    fut.cancel()  # the requester walked away
    release.set()

    # The worker keeps going and still serves the next message.
    fut2 = engine.enqueue("inv6", "b", AgentToolContext(), on_complete=lambda _m: None)
    release.set()
    await asyncio.wait_for(fut2, 3)
    await engine.forget("inv6")


async def test_a_disconnected_requester_does_not_lose_the_whole_turn():
    """The KB-chat path persists from inside the response generator, which
    Starlette CLOSES when the client goes away. So closing the tab mid-answer
    meant ``on_complete`` never ran and the entire turn was lost — while the
    driver kept running to completion, producing an answer nobody could ever see.

    The turn must be persisted whether or not anyone is still listening.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    saved: list[list] = []

    class _Slow:
        async def run(self, content, ctx):  # noqa: ANN001, ANN201
            yield MessageDelta(text="first ")
            started.set()
            await release.wait()
            yield MessageDelta(text="and the rest")
            yield RunDone()

    engine = ChatTurnEngine(_Slow())  # ty: ignore[invalid-argument-type]
    resp = await engine.stream(
        "kb1", "q", AgentToolContext(), on_complete=lambda msgs: saved.append(msgs)
    )

    # Starlette types body_iterator as a plain AsyncIterable; the response we
    # build is an async GENERATOR, and closing it is exactly what Starlette does
    # when the client disconnects.
    body = cast("AsyncGenerator[str]", resp.body_iterator)
    await body.__anext__()  # read the first frame, then walk away
    await asyncio.wait_for(started.wait(), 3)
    await body.aclose()  # what Starlette does on disconnect

    release.set()
    for _ in range(200):  # let the detached drain finish
        if saved:
            break
        await asyncio.sleep(0.01)

    assert saved, "the turn was dropped when the requester disconnected"
    assert "and the rest" in saved[0][0].content


async def test_aclose_lets_an_in_flight_turn_finish_and_save():
    """A pod rollover must not eat a turn that was seconds from done.

    Shutdown cancelled the registered background tasks and tore down sandboxes,
    but nothing tracked or awaited the turn workers — so anything not yet
    persisted went with the process, silently and with no terminal event.
    """
    release = asyncio.Event()
    saved: list[list] = []

    class _Slow:
        async def run(self, content, ctx):  # noqa: ANN001, ANN201
            await release.wait()
            yield MessageDelta(text="finished during shutdown")
            yield RunDone()

    engine = ChatTurnEngine(_Slow())  # ty: ignore[invalid-argument-type]
    engine.enqueue("inv7", "a", AgentToolContext(), on_complete=lambda m: saved.append(m))
    await asyncio.sleep(0.05)  # the turn is running

    release.set()
    await engine.aclose(timeout=3.0)

    assert saved, "the in-flight turn was dropped by shutdown"
    assert "finished during shutdown" in saved[0][0].content


async def test_aclose_is_bounded_and_gives_up_on_a_wedged_turn():
    """Draining is best-effort: a turn that will never finish must not hold the
    pod open past its grace period (k8s would SIGKILL it anyway, which is
    strictly worse — nothing gets to run its teardown)."""

    class _Wedged:
        async def run(self, content, ctx):  # noqa: ANN001, ANN201
            await asyncio.Event().wait()  # never returns
            yield RunDone()

    engine = ChatTurnEngine(_Wedged())  # ty: ignore[invalid-argument-type]
    engine.enqueue("inv8", "a", AgentToolContext(), on_complete=lambda _m: None)
    await asyncio.sleep(0.05)

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await engine.aclose(timeout=0.2)
    assert loop.time() - t0 < 2.0  # returned on its own deadline, not never


async def test_close_streams_lets_a_key_change_be_recovered():
    """A chat's engine key is not stable for its whole life.

    The DEFAULT chat keys on the ITEM id (so item-level endpoints, the workflow
    drive path and file-change broadcasts share its stream); every other chat
    keys on its own id. Delete the current default and another chat BECOMES the
    default — its key flips from `chat_id` to `item_id` while viewers are still
    subscribed under the old one, so every later turn publishes into a session
    they are not listening to. Deaf, with a healthy-looking heartbeat.

    `close_streams` is how the caller repairs that: end those responses, and the
    client's reconnect re-subscribes under the key that is now correct.
    """
    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    ended = asyncio.Event()

    async def read() -> None:
        async for _frame in engine.subscribe_sse("chat-b", heartbeat_interval=5.0):
            pass
        ended.set()

    reader = asyncio.create_task(read())
    await asyncio.sleep(0.05)

    engine.close_streams("chat-b")  # the key just became the item id

    await asyncio.wait_for(ended.wait(), 3)
    reader.cancel()


async def test_close_streams_is_a_no_op_for_a_key_nobody_watches():
    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    engine.close_streams("never-subscribed")  # must not raise or create noise


async def test_a_send_survives_its_request_being_cancelled():
    """A dead connection must not leave a message with no turn.

    `ChatSendService.send` persists the user's message and only THEN does the I/O
    that can outlast the connection — a cold sandbox wake, a slow store, image
    and skill file reads — before enqueuing. A request cancelled in that window
    left the message persisted and the turn never created, so the composer stayed
    locked forever waiting for a reply nobody was going to produce. No amount of
    client-side recovery can invent a turn that does not exist.
    """
    from workspace_app.api.chat_send import ChatSendService

    finished = asyncio.Event()
    started = asyncio.Event()

    service = object.__new__(ChatSendService)  # the protection, not the wiring
    service._inflight = set()

    async def _slow(*_a, **_k) -> None:  # noqa: ANN002, ANN003
        started.set()
        await asyncio.sleep(0.1)  # the window between persist and enqueue
        finished.set()

    service._send = _slow

    caller = asyncio.create_task(
        service.send("inv", "c1", cast("Any", None), "inv", cast("Any", None))
    )
    await asyncio.wait_for(started.wait(), 3)
    caller.cancel()  # the client hung up

    await asyncio.wait_for(finished.wait(), 3)


async def test_a_send_still_reports_its_own_failure_to_a_live_request():
    """Shielding must not swallow errors: a request that is still there has to
    see the exception, or a bad send would silently 202."""
    from workspace_app.api.chat_send import ChatSendService

    service = object.__new__(ChatSendService)
    service._inflight = set()

    async def _boom(*_a, **_k) -> None:  # noqa: ANN002, ANN003
        raise RuntimeError("bad request")

    service._send = _boom

    try:
        await service.send("inv", "c1", cast("Any", None), "inv", cast("Any", None))
    except RuntimeError as exc:
        assert "bad request" in str(exc)
    else:  # pragma: no cover - the assertion below names the failure
        raise AssertionError("the send's failure never reached the caller")
