"""#349 — turn cancellation must work ACROSS pods.

When nginx sticky routing fails (sub-path rewrite, rollout, HPA reshuffle), a new
message / Stop lands on a *different* replica than the one running the turn. The
in-pod `asyncio.Task` handle is then unreachable, so the old per-pod cancel was a
no-op and two concurrent turns ran. The fix shares a monotonic `epoch` via
`ITurnControl`; a per-turn watcher on the owning pod polls it and aborts once the
epoch advances past the turn's stamp.

Two `ChatTurnEngine` instances sharing ONE `InMemoryTurnControl` simulate two
pods over a shared backend — no broker needed. A tiny `poll_interval` keeps the
watcher snappy under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from workspace_app.agent import AgentToolContext
from workspace_app.api import MessageDelta, RunDone
from workspace_app.api.events import AgentEvent
from workspace_app.api.turns import ChatTurnEngine
from workspace_app.turn_control import InMemoryTurnControl


class _BlockingRunner:
    """Yield one event, announce it, then hang until cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, content: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text=content)
        self.started.set()
        await asyncio.sleep(30)  # hang until the watcher cancels us
        yield RunDone()  # pragma: no cover — never reached


class _FastRunner:
    async def run(self, content: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text=content)
        yield RunDone()


def _pod(runner, control: InMemoryTurnControl) -> ChatTurnEngine:
    """A 'pod': a ChatTurnEngine over the shared control with a snappy cross-pod
    poll, fed a duck-typed scripted runner."""
    return ChatTurnEngine(runner, turn_control=control, poll_interval=0.01)


async def _consume(resp) -> None:
    async for _ in resp.body_iterator:
        pass


async def test_new_message_on_another_pod_cancels_the_in_flight_turn():
    """KB-chat supersede across pods: pod B's new message cancels pod A's
    in-flight turn for the same key, even though pod A owns the task."""
    control = InMemoryTurnControl()
    runner_a = _BlockingRunner()
    pod_a = _pod(runner_a, control)
    pod_b = _pod(_FastRunner(), control)

    captured: list = []
    resp_a = await pod_a.stream("K", "one", AgentToolContext(), on_complete=captured.extend)
    drain_a = asyncio.create_task(_consume(resp_a))
    await asyncio.wait_for(runner_a.started.wait(), 2)

    # New message for the SAME key lands on pod B → supersede.
    resp_b = await pod_b.stream("K", "two", AgentToolContext(), on_complete=lambda m: None)
    await _consume(resp_b)

    await asyncio.wait_for(drain_a, 2)  # pod A's turn was cancelled → stream closed
    assert any(m.role == "error" and m.error_kind == "cancelled" for m in captured)


async def test_stop_on_another_pod_cancels_the_in_flight_kb_turn():
    """Explicit Stop (KB `cancel`) from pod B interrupts pod A's in-flight turn
    even though pod B has no local session for the key."""
    control = InMemoryTurnControl()
    runner_a = _BlockingRunner()
    pod_a = _pod(runner_a, control)
    pod_b = _pod(_FastRunner(), control)

    captured: list = []
    resp_a = await pod_a.stream("K", "one", AgentToolContext(), on_complete=captured.extend)
    drain_a = asyncio.create_task(_consume(resp_a))
    await asyncio.wait_for(runner_a.started.wait(), 2)

    await pod_b.cancel("K")  # Stop pressed; request routed to the wrong pod

    await asyncio.wait_for(drain_a, 2)
    assert any(m.role == "error" and m.error_kind == "cancelled" for m in captured)


async def test_stop_on_another_pod_cancels_the_in_flight_rca_turn():
    """RCA workspace Stop (`cancel_current`) from pod B interrupts the
    collaborative turn running in pod A's queue worker."""
    control = InMemoryTurnControl()
    runner_a = _BlockingRunner()
    pod_a = _pod(runner_a, control)
    pod_b = _pod(_FastRunner(), control)

    captured: list = []
    done = asyncio.Event()

    def on_complete(msgs):
        captured.extend(msgs)
        done.set()

    pod_a.enqueue("K", "slow", AgentToolContext(), on_complete=on_complete)
    await asyncio.wait_for(runner_a.started.wait(), 2)

    await pod_b.cancel_current("K")  # Stop routed to a pod without the turn

    await asyncio.wait_for(done.wait(), 2)
    await pod_a.forget("K")
    assert any(m.role == "error" and m.error_kind == "cancelled" for m in captured)


async def test_delete_on_another_pod_cancels_the_in_flight_turn():
    """Deleting a chat (`forget`) on pod B aborts the turn still running on pod A
    — otherwise it would keep running against a conversation that no longer
    exists."""
    control = InMemoryTurnControl()
    runner_a = _BlockingRunner()
    pod_a = _pod(runner_a, control)
    pod_b = _pod(_FastRunner(), control)

    captured: list = []
    resp_a = await pod_a.stream("K", "one", AgentToolContext(), on_complete=captured.extend)
    drain_a = asyncio.create_task(_consume(resp_a))
    await asyncio.wait_for(runner_a.started.wait(), 2)

    await pod_b.forget("K")  # chat deleted; delete hit a pod without the turn

    await asyncio.wait_for(drain_a, 2)
    assert any(m.role == "error" and m.error_kind == "cancelled" for m in captured)


async def test_new_rca_message_on_another_pod_does_not_supersede_the_running_turn():
    """RCA workspace turns SERIALIZE, they don't cancel each other (#43) — so a
    new collaborative message landing on a peer pod must NOT abort the in-flight
    turn. This pins the `_worker` "stamp current, don't bump" rule."""
    control = InMemoryTurnControl()
    release = asyncio.Event()
    started = asyncio.Event()

    class _SlowRunner:
        async def run(self, content: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            yield MessageDelta(text=content)
            started.set()
            await release.wait()
            yield RunDone()

    pod_a = _pod(_SlowRunner(), control)
    pod_b = _pod(_FastRunner(), control)

    cap_a: list = []
    done_a = asyncio.Event()

    def capture_a(msgs):
        cap_a.extend(msgs)
        done_a.set()

    pod_a.enqueue("K", "one", AgentToolContext(), on_complete=capture_a)
    await asyncio.wait_for(started.wait(), 2)

    done_b = asyncio.Event()
    pod_b.enqueue("K", "two", AgentToolContext(), on_complete=lambda m: done_b.set())
    await asyncio.wait_for(done_b.wait(), 2)  # the peer's turn ran to completion

    await asyncio.sleep(0.05)  # several poll intervals — an errant watcher would have fired
    assert not done_a.is_set()  # pod A's turn is STILL running — not superseded

    release.set()  # let it finish on its own terms
    await asyncio.wait_for(done_a.wait(), 2)
    await pod_a.forget("K")
    await pod_b.forget("K")
    assert not any(m.role == "error" and m.error_kind == "cancelled" for m in cap_a)
    assert cap_a[0].content == "one"  # completed normally
