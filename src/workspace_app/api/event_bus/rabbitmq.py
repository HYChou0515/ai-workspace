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
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from ...observability.stdout_log import install_own_handler
from ..events import AgentEvent, event_from_dict
from .base import IEventBus, OnEvent

logger = logging.getLogger(__name__)
# its `event_bus: connected` line is what the runbook checks on each pod
install_own_handler(logger)


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

    def publish(self, key: str, origin: str, event: AgentEvent, event_id: str) -> None:
        # Fire-and-forget: serialize the envelope and hand it to the transport. A
        # failure here must NEVER propagate to (or block) the turn — local delivery
        # already happened; a dropped bus event degrades to the store-poll fallback.
        try:
            body = json.dumps(
                {"key": key, "origin": origin, "id": event_id, "event": asdict(event)}
            ).encode()
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
        # `start_consuming` sets `_on_event` BEFORE it calls `transport.start`, and the
        # transport only delivers after `start` — so a frame can never arrive with the
        # consumer unwired. The guard is a type-narrowing (`OnEvent | None`) formality;
        # its None side is unreachable by construction, hence no-branch.
        if self._on_event is not None:  # pragma: no branch
            # `.get`, not `[]`: a frame published by a pod that has not been
            # rolled yet has no id, and an exception here would drop a live
            # event for the length of a rolling upgrade. No id simply means
            # nothing to dedupe on — the old behaviour.
            self._on_event(msg["key"], msg["origin"], event, msg.get("id") or "")


#: Waits between tries to reach the broker: doubling from 1 s up to this.
RECONNECT_MAX_S = 30.0


class AioPikaTransport(IAmqpTransport):
    """aio_pika fanout adapter, exercised against a stand-in aio_pika by the unit
    tests and against a real broker by a broker-gated integration test. aio_pika is
    imported lazily so this module loads without it — a clear ImportError only if
    `event_bus.kind: rabbitmq` is enabled without the `rabbitmq` extra installed.

    `connect_robust` reconnects only a connection that once succeeded: a broker
    not ready when the pod starts (a rollout restarting both) used to end the
    background task for good — said only once, as asyncio's "Task exception was
    never retrieved", with every live event of this pod piling up unsent and
    none coming in. So reaching the broker is retried, each try and the connect
    said in the log, and one failed send never ends the sending."""

    def __init__(
        self,
        *,
        url: str,
        exchange: str,
        queue_max_length: int,
        heartbeat_seconds: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._url = url
        self._exchange_name = exchange
        self._queue_max_length = queue_max_length
        self._heartbeat = heartbeat_seconds
        self._sleep = sleep
        self._wait = 1.0
        self._up = False
        self._outbound: asyncio.Queue[bytes] | None = None  # built on start
        self._on_message: Callable[[bytes], None] | None = None

    def start(self, on_message: Callable[[bytes], None]) -> None:
        self._on_message = on_message
        self._outbound = asyncio.Queue()
        # Background connect + consume + publish drain, on the running loop. Never
        # blocks the caller.
        asyncio.get_event_loop().create_task(self._run())

    def publish(self, body: bytes) -> None:
        # Fire-and-forget: hand off to the outbound queue drained while connected.
        # If the bus hasn't started (no loop/queue yet), drop — the store-poll
        # backstops.
        outbound = self._outbound
        if outbound is not None:
            try:
                outbound.put_nowait(body)
            except Exception:  # noqa: BLE001 - a full/closed queue drops the frame
                logger.warning("event_bus: outbound full, dropped a frame")

    async def _run(self) -> None:
        import importlib

        # Loaded dynamically and pinned to Any so ty stays agnostic to whether the
        # optional aio_pika extra is installed. A static `import aio_pika` makes its
        # unresolved-import suppression flip used/unused across envs, and once the
        # real types ARE visible ty rejects connect_robust(heartbeat=...) — aio_pika's
        # overloads omit heartbeat that the runtime accepts via **kwargs. Typing the
        # handle Any sidesteps both without a stale ignore comment.
        aio_pika: Any = importlib.import_module("aio_pika")
        # Doubles while the broker stays away; a connect starts it over at 1 s.
        self._wait = 1.0
        while True:
            # set once connected: a connection that breaks after that is "lost",
            # one that never came up "cannot reach" — two different causes
            self._up = False
            try:
                await self._connected(aio_pika)
                logger.warning(
                    "event_bus: lost the broker — connecting again in %.0f s; "
                    "until then live events do not cross pods",
                    self._wait,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — said, and tried again
                logger.warning(
                    "event_bus: %s — trying again in %.0f s; until then live events "
                    "do not cross pods",
                    "lost the broker" if self._up else "cannot reach the broker",
                    self._wait,
                    exc_info=True,
                )
            await self._sleep(self._wait)
            self._wait = min(self._wait * 2, RECONNECT_MAX_S)

    async def _connected(self, aio_pika: Any) -> None:
        """One connection, from connect to its end — closed however it ends: a
        robust connection left open would go on reconnecting on its own beside
        the next one."""
        conn = await aio_pika.connect_robust(self._url, heartbeat=self._heartbeat)
        try:
            channel = await conn.channel()
            exchange = await channel.declare_exchange(
                self._exchange_name, aio_pika.ExchangeType.FANOUT, durable=True
            )
            # One exclusive, auto-delete queue per pod (server-named); transient,
            # bounded.
            queue = await channel.declare_queue(
                exclusive=True,
                auto_delete=True,
                arguments={"x-max-length": self._queue_max_length},
            )
            await queue.bind(exchange)
            logger.info(
                "event_bus: connected — exchange %s, this pod's queue %s",
                self._exchange_name,
                queue.name,
            )
            self._wait = 1.0
            self._up = True

            async def _drain_outbound() -> None:
                assert self._outbound is not None
                # Every streamed token is a frame: while the channel is down each
                # fails. One line when the dropping starts, one when it ends —
                # not a traceback per token (review of #938).
                dropped = 0
                while True:
                    body = await self._outbound.get()
                    try:
                        await exchange.publish(aio_pika.Message(body), routing_key="")
                    except Exception:  # noqa: BLE001 — a lost frame, not the sending
                        if not dropped:
                            logger.warning("event_bus: dropping frames on send", exc_info=True)
                        dropped += 1
                        continue
                    if dropped:
                        logger.warning(
                            "event_bus: sending again — %d frame(s) were dropped", dropped
                        )
                        dropped = 0

            drain = asyncio.get_event_loop().create_task(_drain_outbound())
            try:
                async with queue.iterator() as it:
                    async for message in it:
                        async with message.process():
                            if self._on_message is not None:
                                self._on_message(message.body)
            finally:
                drain.cancel()
        finally:
            with contextlib.suppress(Exception):
                await conn.close()
