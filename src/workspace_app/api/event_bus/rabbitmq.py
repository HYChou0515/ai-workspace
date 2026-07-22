"""RabbitMQ `IEventBus` — fanout over the broker so turn events reach every pod.

The bus LOGIC (envelope serialize/deserialize, fire-and-forget, swallow-on-error)
sits behind an `IAmqpTransport` seam so it is unit-testable without a broker; the
real aio_pika adapter (`AioPikaTransport`) is a thin, lazily-imported layer whose
behaviour is exercised only by a broker-gated `@pytest.mark.integration` test.

Topology (in `AioPikaTransport`): a **fanout** exchange + one **exclusive,
auto-delete** queue per pod; every pod receives every event and the ENGINE filters
by key (stateless — no per-key bindings to leak). The future alternative, when
broker bandwidth bites, is a **topic** exchange with per-key bindings so a pod only
receives keys it views — swap `AioPikaTransport` alone; nothing else changes.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from ..events import AgentEvent, event_from_dict
from .base import IEventBus, OnEvent

logger = logging.getLogger(__name__)


class IAmqpTransport(abc.ABC):
    """The broker seam: raw-bytes fanout. `publish` is fire-and-forget; `start`
    begins consuming and calls `on_message(body)` for every received frame."""

    @abc.abstractmethod
    def publish(self, body: bytes) -> None: ...

    @abc.abstractmethod
    def start(self, on_message: Callable[[bytes], None]) -> None: ...


class RabbitMQEventBus(IEventBus):
    def __init__(
        self,
        *,
        url: str,
        exchange: str = "rca_turn_events",
        queue_max_length: int = 10_000,
        heartbeat_seconds: int = 60,
        transport: IAmqpTransport | None = None,
    ) -> None:
        # Lazily build the aio_pika transport so importing this module (and running
        # unit tests with a fake transport) never needs aio_pika installed.
        self._transport = transport or AioPikaTransport(
            url=url,
            exchange=exchange,
            queue_max_length=queue_max_length,
            heartbeat_seconds=heartbeat_seconds,
        )
        self._on_event: OnEvent | None = None

    def start_consuming(self, on_event: OnEvent) -> None:
        self._on_event = on_event
        self._transport.start(self._on_message)

    def publish(self, key: str, origin: str, event: AgentEvent) -> None:
        # Fire-and-forget: serialize the envelope and hand it to the transport. A
        # failure here must NEVER propagate to (or block) the turn — local delivery
        # already happened; a dropped bus event degrades to the store-poll fallback.
        try:
            body = json.dumps({"key": key, "origin": origin, "event": asdict(event)}).encode()
            self._transport.publish(body)
        except Exception:  # noqa: BLE001 — the bus is best-effort, never fails a turn
            logger.warning("event_bus: dropped a publish for key %s", key, exc_info=True)

    def _on_message(self, body: bytes) -> None:
        # One bad frame must not kill the consumer — swallow + log and keep going.
        try:
            msg = json.loads(body)
            event = event_from_dict(msg["event"])
        except Exception:  # noqa: BLE001
            logger.warning("event_bus: dropped a malformed frame", exc_info=True)
            return
        if self._on_event is not None:
            self._on_event(msg["key"], msg["origin"], event)


class AioPikaTransport(IAmqpTransport):
    """aio_pika fanout adapter. Not exercised by unit tests (no broker); a
    broker-gated integration test covers the real path. aio_pika is imported lazily
    so this module loads without it — a clear ImportError only if `event_bus.kind:
    rabbitmq` is enabled without the `rabbitmq` extra installed."""

    def __init__(
        self, *, url: str, exchange: str, queue_max_length: int, heartbeat_seconds: int
    ) -> None:  # pragma: no cover - integration-only
        self._url = url
        self._exchange_name = exchange
        self._queue_max_length = queue_max_length
        self._heartbeat = heartbeat_seconds
        self._outbound: asyncio.Queue[bytes] | None = None  # built on start
        self._on_message: Callable[[bytes], None] | None = None

    def start(self, on_message: Callable[[bytes], None]) -> None:  # pragma: no cover
        self._on_message = on_message
        self._outbound = asyncio.Queue()
        # Background connect + consume + publish drain, on the running loop. Uses
        # RobustConnection so a broker blip auto-reconnects (re-declaring the
        # exclusive/auto-delete queue). Never blocks the caller.
        asyncio.get_event_loop().create_task(self._run())

    def publish(self, body: bytes) -> None:  # pragma: no cover - integration-only
        # Fire-and-forget: hand off to the outbound queue drained by `_run`. If the
        # bus hasn't started (no loop/queue yet), drop — the store-poll backstops.
        outbound = self._outbound
        if outbound is not None:
            try:
                outbound.put_nowait(body)
            except Exception:  # noqa: BLE001 - a full/closed queue drops the frame
                logger.warning("event_bus: outbound full, dropped a frame")

    async def _run(self) -> None:  # pragma: no cover - integration-only
        import importlib

        # Loaded dynamically and pinned to Any so ty stays agnostic to whether the
        # optional aio_pika extra is installed. A static `import aio_pika` makes its
        # unresolved-import suppression flip used/unused across envs, and once the
        # real types ARE visible ty rejects connect_robust(heartbeat=...) — aio_pika's
        # overloads omit heartbeat that the runtime accepts via **kwargs. Typing the
        # handle Any sidesteps both without a stale ignore comment. Integration-only.
        aio_pika: Any = importlib.import_module("aio_pika")

        conn = await aio_pika.connect_robust(self._url, heartbeat=self._heartbeat)
        channel = await conn.channel()
        exchange = await channel.declare_exchange(
            self._exchange_name, aio_pika.ExchangeType.FANOUT, durable=True
        )
        # One exclusive, auto-delete queue per pod (server-named); transient, bounded.
        queue = await channel.declare_queue(
            exclusive=True,
            auto_delete=True,
            arguments={"x-max-length": self._queue_max_length},
        )
        await queue.bind(exchange)

        async def _drain_outbound() -> None:
            assert self._outbound is not None
            while True:
                body = await self._outbound.get()
                await exchange.publish(aio_pika.Message(body), routing_key="")

        asyncio.get_event_loop().create_task(_drain_outbound())
        async with queue.iterator() as it:
            async for message in it:
                async with message.process():
                    if self._on_message is not None:
                        self._on_message(message.body)
