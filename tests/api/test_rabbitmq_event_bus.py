"""RabbitMQEventBus logic (envelope serialize/deserialize, fire-and-forget) via a
fake transport — no broker. The real aio_pika path is a broker-gated integration
test (not run here); this locks the parts that DON'T need a broker.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import pytest

from workspace_app.api.event_bus import AioPikaTransport, IAmqpTransport, RabbitMQEventBus
from workspace_app.api.events import MessageDelta


class _FakeTransport(IAmqpTransport):
    """Records published frames and lets a test hand one back as if received."""

    def __init__(self, *, raise_on_publish: bool = False) -> None:
        self.published: list[bytes] = []
        self._on_message: Callable[[bytes], None] | None = None
        self._raise = raise_on_publish

    def publish(self, body: bytes) -> None:
        if self._raise:
            raise RuntimeError("broker down")
        self.published.append(body)

    def start(self, on_message: Callable[[bytes], None]) -> None:
        self._on_message = on_message

    def deliver(self, body: bytes) -> None:  # simulate a frame arriving from the broker
        assert self._on_message is not None
        self._on_message(body)


def test_an_event_round_trips_through_the_bus():
    t = _FakeTransport()
    bus = RabbitMQEventBus(url="", transport=t)
    got: list[tuple[str, str, object]] = []
    bus.start_consuming(lambda key, origin, event: got.append((key, origin, event)))

    bus.publish("K", "podA", MessageDelta(text="hi"))
    assert t.published, "the envelope was serialized to the transport"

    # A pod receiving that frame reconstructs (key, origin, the exact event).
    t.deliver(t.published[0])
    assert got == [("K", "podA", MessageDelta(text="hi"))]


def test_publish_is_fire_and_forget_when_the_broker_is_down():
    # A broker failure must never propagate to (or block) the turn.
    bus = RabbitMQEventBus(url="", transport=_FakeTransport(raise_on_publish=True))
    bus.start_consuming(lambda *_: None)
    bus.publish("K", "podA", MessageDelta(text="hi"))  # must NOT raise


def test_a_malformed_frame_is_dropped_not_fatal():
    t = _FakeTransport()
    bus = RabbitMQEventBus(url="", transport=t)
    got: list[object] = []
    bus.start_consuming(lambda *a: got.append(a))

    t.deliver(b"not json at all")  # must be swallowed
    t.deliver(b'{"key":"K","origin":"A","event":{"type":"nope"}}')  # unknown type
    assert got == []  # neither reached the consumer, and neither raised


def test_defaults_to_the_aio_pika_transport():
    # With no injected transport, the real (lazy) aio_pika adapter is built — it only
    # stores config here (no connect until `start`), so this needs no broker.
    bus = RabbitMQEventBus(url="amqp://guest:guest@localhost/")
    assert isinstance(bus._transport, AioPikaTransport)


@pytest.mark.integration
async def test_real_broker_cross_pod_round_trip():
    """The REAL aio_pika fanout path — needs aio_pika installed + a live broker at
    $AMQP_URL. Two buses = two pods; a publish on one reaches the other's consumer.
    Skipped (not failed) without a broker, so CI/unit runs are unaffected; this is
    the only coverage of AioPikaTransport, exercised in a broker-equipped env."""
    pytest.importorskip("aio_pika")
    url = os.environ.get("AMQP_URL")
    if not url:
        pytest.skip("no $AMQP_URL — needs a live RabbitMQ broker")

    received: list[tuple[str, str, object]] = []
    got = asyncio.Event()
    bus_a = RabbitMQEventBus(url=url, exchange="rca_test_events")
    bus_b = RabbitMQEventBus(url=url, exchange="rca_test_events")

    def on_event(key: str, origin: str, event: object) -> None:
        received.append((key, origin, event))
        got.set()

    bus_a.start_consuming(lambda *_: None)
    bus_b.start_consuming(on_event)
    await asyncio.sleep(1.0)  # let both queues bind to the exchange

    bus_a.publish("K", "podA", MessageDelta(text="real"))
    await asyncio.wait_for(got.wait(), 5)
    assert received == [("K", "podA", MessageDelta(text="real"))]
