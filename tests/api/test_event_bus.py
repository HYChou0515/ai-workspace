"""Cross-pod live streaming via the event bus.

Two `ChatTurnEngine`s sharing ONE `InMemoryEventBus` are two pods over one broker.
A turn on one pod must stream to a viewer whose SSE landed on the OTHER pod — the
whole point of the bus, and what fixes the cross-pod-blind "還在準備" symptom.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from workspace_app.api import MessageDelta
from workspace_app.api.event_bus import InMemoryEventBus
from workspace_app.api.turns import ChatTurnEngine


class _Runner:
    """Never invoked — these tests drive publish/subscribe directly."""

    async def run(self, content, ctx):  # noqa: ANN001, ANN201
        if False:  # pragma: no cover
            yield None


async def _read_frame(gen: Any) -> dict[str, Any]:
    while True:
        frame = await asyncio.wait_for(gen.__anext__(), 3)
        if frame.startswith(":"):
            continue
        assert frame.startswith("data: "), frame
        return json.loads(frame[len("data: ") :].strip())


async def test_a_turn_on_one_pod_streams_to_a_viewer_on_another_pod():
    bus = InMemoryEventBus()
    pod_a = ChatTurnEngine(_Runner(), event_bus=bus, pod_id="A")  # ty: ignore[invalid-argument-type]
    pod_b = ChatTurnEngine(_Runner(), event_bus=bus, pod_id="B")  # ty: ignore[invalid-argument-type]
    key = "inv"

    # A viewer whose SSE landed on pod B.
    sub = pod_b.subscribe_sse(key, heartbeat_interval=5.0)
    # A turn running on pod A publishes an event (A holds the turn's session).
    pod_a._ws_session(key).publish(MessageDelta(text="hi from A"))

    frame = await _read_frame(sub)
    assert frame["text"] == "hi from A"  # crossed pods via the bus

    await sub.aclose()
    await pod_a.forget(key)
    await pod_b.forget(key)


async def test_a_same_pod_viewer_gets_each_event_once_not_doubled():
    # The turn's pod delivers locally AND publishes to the bus; the bus fans back to
    # the origin too, so skip-own must stop the origin re-delivering its own event.
    bus = InMemoryEventBus()
    pod_a = ChatTurnEngine(_Runner(), event_bus=bus, pod_id="A")  # ty: ignore[invalid-argument-type]
    key = "inv"
    sub = pod_a.subscribe_sse(key, heartbeat_interval=0.05)  # viewer on the SAME pod
    pod_a._ws_session(key).publish(MessageDelta(text="once"))

    f = await _read_frame(sub)
    assert f["text"] == "once"
    nxt = await asyncio.wait_for(sub.__anext__(), 3)
    assert nxt.startswith(":"), nxt  # a heartbeat, NOT a duplicate "once" (skip-own)

    await sub.aclose()
    await pod_a.forget(key)


async def test_events_do_not_cross_between_chats():
    # Routing is per engine-key: a viewer of chat K2 must not receive chat K1's events.
    bus = InMemoryEventBus()
    pod_a = ChatTurnEngine(_Runner(), event_bus=bus, pod_id="A")  # ty: ignore[invalid-argument-type]
    pod_b = ChatTurnEngine(_Runner(), event_bus=bus, pod_id="B")  # ty: ignore[invalid-argument-type]
    sub_k2 = pod_b.subscribe_sse("K2", heartbeat_interval=0.05)
    pod_a._ws_session("K1").publish(MessageDelta(text="for K1"))

    frame = await asyncio.wait_for(sub_k2.__anext__(), 3)
    assert frame.startswith(":"), frame  # K2's viewer gets a heartbeat, not K1's event

    await sub_k2.aclose()
    await pod_a.forget("K1")
    await pod_b.forget("K2")


async def test_two_viewers_of_one_chat_on_different_pods_both_stream():
    bus = InMemoryEventBus()
    pod_a = ChatTurnEngine(_Runner(), event_bus=bus, pod_id="A")  # ty: ignore[invalid-argument-type]
    pod_b = ChatTurnEngine(_Runner(), event_bus=bus, pod_id="B")  # ty: ignore[invalid-argument-type]
    key = "inv"
    sub_a = pod_a.subscribe_sse(key, heartbeat_interval=5.0)
    sub_b = pod_b.subscribe_sse(key, heartbeat_interval=5.0)
    pod_a._ws_session(key).publish(MessageDelta(text="both"))  # turn on A

    fa = await _read_frame(sub_a)  # local
    fb = await _read_frame(sub_b)  # via the bus
    assert fa["text"] == "both"
    assert fb["text"] == "both"

    await sub_a.aclose()
    await sub_b.aclose()
    await pod_a.forget(key)
    await pod_b.forget(key)


class _CountingBus(InMemoryEventBus):
    """Records the origin of every publish, to prove a bus-delivered event is not
    re-published (which would storm)."""

    def __init__(self) -> None:
        super().__init__()
        self.origins: list[str] = []

    def publish(self, key: str, origin: str, event: Any) -> None:
        self.origins.append(origin)
        super().publish(key, origin, event)


async def test_a_bus_delivered_event_is_not_republished():
    # The single most dangerous bug: if the consumer re-published, it would storm.
    bus = _CountingBus()
    pod_a = ChatTurnEngine(_Runner(), event_bus=bus, pod_id="A")  # ty: ignore[invalid-argument-type]
    pod_b = ChatTurnEngine(_Runner(), event_bus=bus, pod_id="B")  # ty: ignore[invalid-argument-type]
    key = "inv"
    sub_b = pod_b.subscribe_sse(key, heartbeat_interval=5.0)
    pod_a._ws_session(key).publish(MessageDelta(text="x"))
    await _read_frame(sub_b)  # B received it via the bus

    assert bus.origins == ["A"]  # published exactly once (by A); B did NOT re-publish

    await sub_b.aclose()
    await pod_a.forget(key)
    await pod_b.forget(key)


async def test_single_pod_bus_is_a_noop():
    # A lone engine (default in-memory bus): publish delivers locally, the bus fans
    # back to the only consumer (itself), skip-own drops it → exactly today's behavior.
    engine = ChatTurnEngine(_Runner())  # ty: ignore[invalid-argument-type]
    key = "inv"
    sub = engine.subscribe_sse(key, heartbeat_interval=0.05)
    engine._ws_session(key).publish(MessageDelta(text="solo"))

    f = await _read_frame(sub)
    assert f["text"] == "solo"
    nxt = await asyncio.wait_for(sub.__anext__(), 3)
    assert nxt.startswith(":"), nxt  # heartbeat, no duplicate — bus is a no-op

    await sub.aclose()
    await engine.forget(key)
