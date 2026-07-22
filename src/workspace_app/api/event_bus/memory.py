"""In-memory `IEventBus` — the default backend (single pod / tests, zero infra).

Single pod: the only registered consumer is the engine itself, and its skip-own
makes the bus a no-op (all delivery is local). Tests: two `ChatTurnEngine`s sharing
ONE instance simulate two pods — a publish invokes BOTH consumers, so an event from
one engine reaches the other's subscribers. Mirrors how `InMemoryTurnControl` lets
two engines share one backend to exercise cross-pod behaviour without a broker.
"""

from __future__ import annotations

from ..events import AgentEvent
from .base import IEventBus, OnEvent


class InMemoryEventBus(IEventBus):
    def __init__(self) -> None:
        self._consumers: list[OnEvent] = []

    def start_consuming(self, on_event: OnEvent) -> None:
        self._consumers.append(on_event)

    def publish(self, key: str, origin: str, event: AgentEvent) -> None:
        # Fanout: invoke every registered consumer (each skips its own origin). Copy
        # the list so a consumer registering mid-dispatch can't mutate the iteration.
        for on_event in list(self._consumers):
            on_event(key, origin, event)
