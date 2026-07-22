"""`get_event_bus` selects the cross-pod bus backend from config: `memory` (default)
or `rabbitmq` (reusing the broker url), so the knob actually wires an impl."""

from __future__ import annotations

import pytest

from workspace_app.api.event_bus import InMemoryEventBus, RabbitMQEventBus
from workspace_app.config.schema import (
    EventBusSettings,
    MessageQueueSettings,
    RabbitmqSettings,
    Settings,
)
from workspace_app.factories import get_event_bus


def test_default_is_the_in_memory_bus():
    assert isinstance(get_event_bus(Settings()), InMemoryEventBus)


def test_schema_default_kind_is_memory():
    # The dataclass default is the single source of truth (unchanged today's behavior).
    assert EventBusSettings().kind == "memory"


def test_rabbitmq_kind_builds_the_rabbitmq_bus_reusing_the_broker_url():
    settings = Settings(
        event_bus=EventBusSettings(kind="rabbitmq"),  # url unset → reuse below
        message_queue=MessageQueueSettings(rabbitmq=RabbitmqSettings(url="amqp://u:p@h/")),
    )
    bus = get_event_bus(settings)
    assert isinstance(bus, RabbitMQEventBus)
    assert bus._transport._url == "amqp://u:p@h/"  # reused message_queue.rabbitmq.url


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="event_bus.kind"):
        get_event_bus(Settings(event_bus=EventBusSettings(kind="nope")))
