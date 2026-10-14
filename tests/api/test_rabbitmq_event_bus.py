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
    got: list[tuple[str, str, object, str]] = []
    bus.start_consuming(lambda key, origin, event, eid: got.append((key, origin, event, eid)))

    bus.publish("K", "podA", MessageDelta(text="hi"), "e1")
    assert t.published, "the envelope was serialized to the transport"

    # A pod receiving that frame reconstructs (key, origin, the exact event) —
    # and the id it was published with, which is what the far-side viewer
    # dedupes on after reconnecting across pods.
    t.deliver(t.published[0])
    assert got == [("K", "podA", MessageDelta(text="hi"), "e1")]


def test_publish_is_fire_and_forget_when_the_broker_is_down():
    # A broker failure must never propagate to (or block) the turn.
    bus = RabbitMQEventBus(url="", transport=_FakeTransport(raise_on_publish=True))
    bus.start_consuming(lambda *_: None)
    bus.publish("K", "podA", MessageDelta(text="hi"), "e1")  # must NOT raise


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

    received: list[tuple[str, str, object, str]] = []
    got = asyncio.Event()
    bus_a = RabbitMQEventBus(url=url, exchange="rca_test_events")
    bus_b = RabbitMQEventBus(url=url, exchange="rca_test_events")

    def on_event(key: str, origin: str, event: object, event_id: str) -> None:
        received.append((key, origin, event, event_id))
        got.set()

    bus_a.start_consuming(lambda *_: None)
    bus_b.start_consuming(on_event)
    await asyncio.sleep(1.0)  # let both queues bind to the exchange

    bus_a.publish("K", "podA", MessageDelta(text="real"), "e1")
    await asyncio.wait_for(got.wait(), 5)
    assert received == [("K", "podA", MessageDelta(text="real"), "e1")]


def test_a_frame_from_a_pod_that_predates_event_ids_still_delivers():
    """A rolling upgrade runs both versions at once. An old pod publishes an
    envelope with no `id`, and raising on it would drop LIVE events for the
    length of the rollout — a worse fault than the duplicate bubble the id
    exists to prevent. No id just means nothing to dedupe on."""
    import json

    t = _FakeTransport()
    bus = RabbitMQEventBus(url="", transport=t)
    got: list[tuple[str, str, object, str]] = []
    bus.start_consuming(lambda key, origin, event, eid: got.append((key, origin, event, eid)))

    old_frame = json.dumps(
        {"key": "K", "origin": "podOld", "event": {"type": "message_delta", "text": "hi"}}
    ).encode()
    t.deliver(old_frame)

    assert got == [("K", "podOld", MessageDelta(text="hi"), "")]


# ── the aio_pika transport, against a stand-in aio_pika (no broker) ─────────
#
# A pod whose first connect failed (the broker not ready at rollout) stayed
# without a bus for its whole life, silently: the task died, publishes piled up
# in the outbound queue, nothing came in — and nothing said so. It is what a
# viewer that sees a turn only after a reload looks like (the person's report).


class _FakeAioPika:
    """Just enough of aio_pika for `AioPikaTransport._run`."""

    class ExchangeType:
        FANOUT = "fanout"

    class Message:
        def __init__(self, body: bytes) -> None:
            self.body = body

    def __init__(
        self,
        *,
        connect_failures: int = 0,
        publish_failures: int = 0,
        fail_connects: frozenset[int] = frozenset(),
        declare_fails_on: frozenset[int] = frozenset(),
        stream_ends_on: frozenset[int] = frozenset(),
        stream_raises_on: frozenset[int] = frozenset(),
    ) -> None:
        self.connects = 0
        self._connect_failures = connect_failures
        self._publish_failures = publish_failures
        self._fail_connects = fail_connects
        self._declare_fails_on = declare_fails_on
        self._stream_ends_on = stream_ends_on
        self._stream_raises_on = stream_raises_on
        self.open: list[_FakeConnection] = []
        self.sent: list[bytes] = []
        self.incoming: asyncio.Queue[bytes] = asyncio.Queue()

    async def connect_robust(self, url, heartbeat):  # noqa: ANN001, ANN201
        self.connects += 1
        if self.connects <= self._connect_failures or self.connects in self._fail_connects:
            raise ConnectionError("broker not ready")
        conn = _FakeConnection(self, self.connects)
        self.open.append(conn)
        return conn


class _FakeConnection:
    def __init__(self, pika: _FakeAioPika, n: int) -> None:
        self._pika = pika
        self.n = n

    async def channel(self):  # noqa: ANN201
        if self.n in self._pika._declare_fails_on:
            raise ConnectionError("channel refused")
        return _FakeChannel(self._pika, self.n)

    async def close(self) -> None:
        self._pika.open.remove(self)


class _FakeChannel:
    def __init__(self, pika: _FakeAioPika, n: int) -> None:
        self._pika = pika
        self._n = n

    async def declare_exchange(self, name, kind, durable):  # noqa: ANN001, ANN201
        return _FakeExchange(self._pika)

    async def declare_queue(self, **_kw):  # noqa: ANN201
        return _FakeQueue(
            self._pika,
            ends=self._n in self._pika._stream_ends_on,
            raises=self._n in self._pika._stream_raises_on,
        )


class _FakeExchange:
    def __init__(self, pika: _FakeAioPika) -> None:
        self._pika = pika

    async def publish(self, message, routing_key):  # noqa: ANN001, ANN201
        if self._pika._publish_failures:
            self._pika._publish_failures -= 1
            raise ConnectionError("channel closed")
        self._pika.sent.append(message.body)


class _FakeMessage:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def process(self):  # noqa: ANN201
        import contextlib

        return contextlib.nullcontext()


class _FakeQueue:
    name = "amq.gen-this-pod"

    def __init__(self, pika: _FakeAioPika, *, ends: bool = False, raises: bool = False) -> None:
        self._pika = pika
        self._ends = ends
        self._raises = raises

    async def bind(self, exchange):  # noqa: ANN001, ANN201
        pass

    def iterator(self):  # noqa: ANN201
        pika = self._pika
        ends = self._ends
        raises = self._raises

        class _It:
            async def __aenter__(self):  # noqa: ANN204
                return self

            async def __aexit__(self, *a):  # noqa: ANN001, ANN204
                return False

            def __aiter__(self):  # noqa: ANN204
                return self

            async def __anext__(self):  # noqa: ANN204
                if ends:
                    raise StopAsyncIteration
                if raises:
                    raise ConnectionError("connection reset by the broker")
                return _FakeMessage(await pika.incoming.get())

        return _It()


def _transport(
    monkeypatch, pika: _FakeAioPika, waits: list[float] | None = None
) -> AioPikaTransport:
    import importlib

    real = importlib.import_module
    monkeypatch.setattr(
        importlib, "import_module", lambda name: pika if name == "aio_pika" else real(name)
    )

    async def no_wait(s: float) -> None:
        if waits is not None:
            waits.append(s)
        await asyncio.sleep(0)

    return AioPikaTransport(
        url="amqp://broker", exchange="ex", queue_max_length=10, heartbeat_seconds=5, sleep=no_wait
    )


async def _until(cond, *, tries: int = 200) -> None:  # noqa: ANN001
    for _ in range(tries):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


async def test_a_broker_not_ready_at_start_is_tried_again_and_said(monkeypatch, caplog):
    pika = _FakeAioPika(connect_failures=2)
    t = _transport(monkeypatch, pika)
    got: list[bytes] = []
    with caplog.at_level("INFO", logger="workspace_app.api.event_bus.rabbitmq"):
        t.start(got.append)
        t.publish(b"frame-1")
        await _until(lambda: pika.sent == [b"frame-1"])
        await pika.incoming.put(b"from-another-pod")
        await _until(lambda: got == [b"from-another-pod"])

    assert pika.connects == 3
    text = caplog.text
    assert text.count("cannot reach the broker") == 2
    assert "connected" in text and "amq.gen-this-pod" in text


async def test_a_failed_send_is_said_and_the_next_one_still_goes(monkeypatch, caplog):
    pika = _FakeAioPika(publish_failures=1)
    t = _transport(monkeypatch, pika)
    with caplog.at_level("INFO", logger="workspace_app.api.event_bus.rabbitmq"):
        t.start(lambda _b: None)
        t.publish(b"lost")
        t.publish(b"kept")
        await _until(lambda: pika.sent == [b"kept"])

    assert "dropping frames" in caplog.text


# A connection that fails after it opened (the channel refused) is closed
# before the next try: a robust connection left behind reconnects on its own,
# forever, beside the new one.
async def test_a_connect_that_fails_part_way_is_closed_before_the_next(monkeypatch, caplog):
    pika = _FakeAioPika(declare_fails_on=frozenset({1}))
    t = _transport(monkeypatch, pika)
    with caplog.at_level("INFO", logger="workspace_app.api.event_bus.rabbitmq"):
        t.start(lambda _b: None)
        await _until(lambda: "connected" in caplog.text)

    assert pika.connects == 2
    assert [c.n for c in pika.open] == [2]


# A stream that ends (the broker closed it) is said and connected again after a
# wait — not at once, in a loop, in silence — and its connection is closed.
async def test_a_stream_that_ends_is_said_and_connected_again(monkeypatch, caplog):
    pika = _FakeAioPika(stream_ends_on=frozenset({1}))
    t = _transport(monkeypatch, pika, waits := [])
    got: list[bytes] = []
    with caplog.at_level("INFO", logger="workspace_app.api.event_bus.rabbitmq"):
        t.start(got.append)
        await _until(lambda: pika.connects == 2)
        await pika.incoming.put(b"after")
        await _until(lambda: got == [b"after"])

    assert "lost the broker" in caplog.text
    assert waits == [1.0]
    assert [c.n for c in pika.open] == [2]


# Each outage starts its waits over from 1 s: a pod that was once unlucky must
# not wait 30 s to come back from every later blip.
async def test_the_wait_starts_over_once_connected(monkeypatch):
    pika = _FakeAioPika(fail_connects=frozenset({1, 2, 4}), stream_ends_on=frozenset({3}))
    t = _transport(monkeypatch, pika, waits := [])
    t.start(lambda _b: None)
    await _until(lambda: pika.connects == 5)

    assert waits == [1.0, 2.0, 1.0, 2.0]


# Review of #938: while the broker is away every streamed token fails to send.
# One line when the dropping starts, one when sending is back — not a
# traceback per token.
async def test_a_run_of_failed_sends_is_said_once_and_its_end_with_the_count(monkeypatch, caplog):
    pika = _FakeAioPika(publish_failures=5)
    t = _transport(monkeypatch, pika)
    with caplog.at_level("INFO", logger="workspace_app.api.event_bus.rabbitmq"):
        t.start(lambda _b: None)
        for i in range(6):
            t.publish(f"f{i}".encode())
        await _until(lambda: pika.sent == [b"f5"])

    lines = [r.getMessage() for r in caplog.records]
    assert sum("dropping frames" in m for m in lines) == 1, lines
    assert any("sending again" in m and "5" in m for m in lines), lines


# A connection that was up and then broke is "lost", not "cannot reach": the
# two point an operator at different causes.
async def test_a_connection_that_breaks_after_connecting_is_lost_not_unreachable(
    monkeypatch, caplog
):
    pika = _FakeAioPika(stream_raises_on=frozenset({1}))
    t = _transport(monkeypatch, pika)
    with caplog.at_level("INFO", logger="workspace_app.api.event_bus.rabbitmq"):
        t.start(lambda _b: None)
        await _until(lambda: pika.connects == 2)

    assert "lost the broker" in caplog.text
    assert "cannot reach the broker" not in caplog.text
