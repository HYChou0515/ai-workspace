"""#43 P2: the collaborative turn queue. In a shared investigation, concurrent
messages must SERIALIZE (one turn at a time on the shared sandbox/files) rather
than cancel each other, and Stop must interrupt only the running turn — the
queue keeps draining.
"""

from __future__ import annotations

import asyncio

from workspace_app.agent import AgentToolContext
from workspace_app.api import MessageDelta, RunDone
from workspace_app.api.turns import ChatTurnEngine


async def test_enqueue_runs_queued_turns_in_fifo_order_without_cancelling():
    """Two messages enqueued back-to-back both run, in order, and the first runs
    to completion — a second message does NOT cancel the first (the key
    difference from stream()'s cancel-prior)."""
    order: list[tuple[str, str]] = []

    class _Runner:
        async def run(self, content, ctx):
            order.append(("start", content))
            yield MessageDelta(text=content)
            yield RunDone()
            order.append(("end", content))

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    results: list[list] = []
    both_done = asyncio.Event()

    def on_complete(msgs):
        results.append(msgs)
        if len(results) == 2:
            both_done.set()

    engine.enqueue("inv", "a", AgentToolContext(), on_complete=on_complete)
    engine.enqueue("inv", "b", AgentToolContext(), on_complete=on_complete)
    await asyncio.wait_for(both_done.wait(), 3)
    await engine.forget("inv")

    # 'a' fully finished before 'b' started — serialized, not cancelled.
    assert order == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]
    assert results[0][0].content == "a"
    assert results[1][0].content == "b"


async def test_on_turn_end_runs_after_the_turn_persists():
    """#492: the turn-end flush hook fires once, AFTER on_complete, so durable
    lags by at most one turn (guarantee 2)."""
    seq: list[str] = []
    done = asyncio.Event()

    class _Runner:
        async def run(self, content, ctx):
            yield MessageDelta(text=content)
            yield RunDone()

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]

    def on_complete(msgs):
        seq.append("persist")

    async def on_turn_end():
        seq.append("flush")
        done.set()

    engine.enqueue("inv", "a", AgentToolContext(), on_complete=on_complete, on_turn_end=on_turn_end)
    await asyncio.wait_for(done.wait(), 3)
    await engine.forget("inv")
    assert seq == ["persist", "flush"]


async def test_on_turn_end_failure_does_not_fail_the_turn():
    """A flush failure is best-effort — it must never turn a finished turn into a
    failure or wedge the queue's worker."""
    persisted: list[str] = []
    second_done = asyncio.Event()

    class _Runner:
        async def run(self, content, ctx):
            yield MessageDelta(text=content)
            yield RunDone()

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]

    def on_complete(msgs):
        persisted.append(msgs[0].content)
        if len(persisted) == 2:
            second_done.set()

    async def boom():
        raise RuntimeError("nfs down")

    # First turn's flush raises; the worker must survive and run the second turn.
    engine.enqueue("inv", "a", AgentToolContext(), on_complete=on_complete, on_turn_end=boom)
    engine.enqueue("inv", "b", AgentToolContext(), on_complete=on_complete)
    await asyncio.wait_for(second_done.wait(), 3)
    await engine.forget("inv")
    assert persisted == ["a", "b"]  # both turns completed despite the flush error


async def test_cancel_current_stops_only_the_running_turn_then_next_runs():
    """Stop cancels the in-flight turn (persisted with a cancelled marker) and
    the worker proceeds to the next queued message — one person's Stop doesn't
    clear everyone's queue."""
    started_slow = asyncio.Event()
    results: dict[str, list] = {}
    fast_done = asyncio.Event()

    class _Runner:
        async def run(self, content, ctx):
            if content == "slow":
                started_slow.set()
                await asyncio.sleep(30)  # hang until cancelled
            yield MessageDelta(text=content)
            yield RunDone()

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]

    def oc_slow(msgs):
        results["slow"] = msgs

    def oc_fast(msgs):
        results["fast"] = msgs
        fast_done.set()

    engine.enqueue("inv", "slow", AgentToolContext(), on_complete=oc_slow)
    engine.enqueue("inv", "fast", AgentToolContext(), on_complete=oc_fast)
    await asyncio.wait_for(started_slow.wait(), 3)
    await engine.cancel_current("inv")
    await asyncio.wait_for(fast_done.wait(), 3)
    await engine.forget("inv")

    # the slow turn was cancelled → a cancelled error marker is persisted
    assert any(m.role == "error" and m.error_kind == "cancelled" for m in results["slow"])
    # the next queued turn still ran to completion
    assert results["fast"][0].content == "fast"


async def test_all_subscribers_receive_a_turns_broadcast_events():
    """#43 broadcast: every viewer subscribed to an investigation sees the SAME
    live events for a turn, regardless of who sent it."""

    class _Runner:
        async def run(self, content, ctx):
            yield MessageDelta(text="hi")
            yield RunDone()

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    sub1 = engine.subscribe("inv")  # registers its queue synchronously
    sub2 = engine.subscribe("inv")

    async def collect(sub, n):
        out = []
        async for ev in sub:
            out.append(ev)
            if len(out) >= n:
                return out
        return out

    t1 = asyncio.create_task(collect(sub1, 2))
    t2 = asyncio.create_task(collect(sub2, 2))
    engine.enqueue("inv", "q", AgentToolContext(), on_complete=lambda m: None)
    got1, got2 = await asyncio.wait_for(asyncio.gather(t1, t2), 3)
    await engine.forget("inv")

    assert [type(e).__name__ for e in got1] == ["MessageDelta", "RunDone"]
    assert got1 == got2  # both viewers saw identical events


async def test_subscribe_sse_yields_sse_encoded_frames():
    """subscribe_sse wraps subscribe with SSE encoding so the endpoint is a
    trivial StreamingResponse wrapper."""

    class _Runner:
        async def run(self, content, ctx):
            yield MessageDelta(text="hi")
            yield RunDone()

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    frames = engine.subscribe_sse("inv")

    async def collect(n):
        out: list[str] = []
        async for f in frames:
            out.append(f)
            if len(out) >= n:
                return out
        return out

    task = asyncio.create_task(collect(2))
    engine.enqueue("inv", "q", AgentToolContext(), on_complete=lambda m: None)
    got = await asyncio.wait_for(task, 3)
    await engine.forget("inv")
    assert all(f.startswith("data: ") for f in got)
    assert '"message_delta"' in got[0]


async def test_forget_on_an_unknown_key_is_a_noop():
    """forget for a key that never enqueued/subscribed (no workspace session)
    returns cleanly — e.g. closing a KB chat, which uses stream() not the queue."""

    class _Runner:
        async def run(self, content, ctx):  # pragma: no cover — never invoked
            yield RunDone()

    await ChatTurnEngine(_Runner()).forget("never-touched")  # ty: ignore[invalid-argument-type]


async def test_all_providers_failed_surfaces_a_readable_busy_message():
    """When the runner exhausts the whole failover chain (every model busy →
    AllProvidersFailed), the turn persists a human-readable "models are busy"
    message instead of the raw exception class name (#196-followup)."""
    from workspace_app.failover.core import AllProvidersFailed

    class _Runner:
        async def run(self, content, ctx):
            raise AllProvidersFailed("all providers failed or were cooling")
            yield  # pragma: no cover — marks this an async generator

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    captured: list = []
    done = asyncio.Event()

    def on_complete(msgs):
        captured.extend(msgs)
        done.set()

    engine.enqueue("inv", "q", AgentToolContext(), on_complete=on_complete)
    await asyncio.wait_for(done.wait(), 3)
    await engine.forget("inv")

    errs = [m for m in captured if m.role == "error"]
    assert errs, "a terminal error message should be persisted"
    assert "busy" in errs[-1].content.lower()
    assert "AllProvidersFailed" not in errs[-1].content  # the raw class is hidden


async def test_busy_failure_is_detected_even_when_wrapped_by_the_runner():
    """The SDK may re-raise the failover exhaustion wrapped in another error; the
    readable busy message is still produced by walking the cause chain."""
    from workspace_app.failover.core import AllProvidersFailed

    class _Runner:
        async def run(self, content, ctx):
            try:
                raise AllProvidersFailed("exhausted")
            except AllProvidersFailed as cause:
                raise RuntimeError("model error") from cause
            yield  # pragma: no cover — marks this an async generator

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    captured: list = []
    done = asyncio.Event()

    def on_complete(msgs):
        captured.extend(msgs)
        done.set()

    engine.enqueue("inv", "q", AgentToolContext(), on_complete=on_complete)
    await asyncio.wait_for(done.wait(), 3)
    await engine.forget("inv")

    errs = [m for m in captured if m.role == "error"]
    assert errs and "busy" in errs[-1].content.lower()


async def test_cancel_current_when_idle_is_a_noop():
    """Stop after a turn already finished (session exists, nothing in flight) is
    a clean no-op."""

    class _Runner:
        async def run(self, content, ctx):
            yield MessageDelta(text="done")
            yield RunDone()

    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    finished = asyncio.Event()
    engine.enqueue("inv", "x", AgentToolContext(), on_complete=lambda m: finished.set())
    await asyncio.wait_for(finished.wait(), 3)
    await engine.cancel_current("inv")  # session exists, current_turn is None → no-op
    await engine.forget("inv")
