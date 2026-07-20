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
