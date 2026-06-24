"""The shared process-global cooldown registry."""

from __future__ import annotations

from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.failover.registry import get_cooldown_registry


def test_returns_a_cooldown_registry():
    assert isinstance(get_cooldown_registry(), CooldownRegistry)


def test_is_a_process_global_singleton():
    assert get_cooldown_registry() is get_cooldown_registry()  # same instance every call
