"""Cross-pod live event bus (`plan-event-bus-cross-pod-streaming.md`).

The `#43` broadcast keeps a turn's live events only in the running pod's in-memory
`_ws_sessions`, so a viewer's SSE on any other pod is blind for the whole turn when
sticky routing is degraded. `IEventBus` fans each turn event out to EVERY pod, so a
viewer streams regardless of which pod runs the turn. It is `ITurnControl`-shaped:
an `InMemoryEventBus` default (single pod / tests, zero infra) and a
`RabbitMQEventBus` (fanout over the broker) for multipod, injected via `create_app`.
"""

from __future__ import annotations

import abc
from collections.abc import Callable

from ..events import AgentEvent

# A pod's consumer callback: (engine_key, origin_pod_id, event). The bus invokes it
# for EVERY published event (fanout); the consumer skips its own origin and demuxes
# by key to that pod's local subscribers.
OnEvent = Callable[[str, str, AgentEvent], None]


class IEventBus(abc.ABC):
    """Fan live turn events across pods. Publishing is fire-and-forget — a turn must
    never block on (or fail because of) the bus; local delivery is separate and
    always happens. Consuming is one callback per pod, invoked for every event."""

    @abc.abstractmethod
    def publish(self, key: str, origin: str, event: AgentEvent) -> None:
        """Fan `event` (for engine `key`) out to every pod, INCLUDING the publisher.
        `origin` is the publishing pod's id so a consumer can skip its own (it has
        already delivered the event locally). Fire-and-forget: never raises to the
        caller, never blocks the turn."""

    @abc.abstractmethod
    def start_consuming(self, on_event: OnEvent) -> None:
        """Register this pod's consumer callback. The bus calls it for every event
        published by any pod (fanout). Called once when the engine is built."""
