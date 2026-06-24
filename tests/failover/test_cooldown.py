"""CooldownRegistry — a clock-injected, in-memory "this (model,endpoint) is
hot, skip it for a bit" table shared across all failover roles."""

from __future__ import annotations

from workspace_app.failover.cooldown import CooldownRegistry


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_unmarked_key_is_not_cooling():
    reg = CooldownRegistry(clock=_Clock())
    assert reg.is_cooling(("qwen", "ep1")) is False


def test_marked_key_is_cooling_until_it_expires():
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    reg.mark(("qwen", "ep1"), 30.0)
    assert reg.is_cooling(("qwen", "ep1")) is True
    clock.now = 29.9
    assert reg.is_cooling(("qwen", "ep1")) is True
    clock.now = 30.0  # deadline reached → no longer cooling
    assert reg.is_cooling(("qwen", "ep1")) is False


def test_cooldown_is_per_key():
    reg = CooldownRegistry(clock=_Clock())
    reg.mark(("qwen", "ep1"), 30.0)
    assert reg.is_cooling(("qwen", "ep1")) is True
    assert reg.is_cooling(("qwen", "ep2")) is False  # different endpoint untouched


def test_remarking_extends_the_deadline():
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    reg.mark(("qwen", "ep1"), 30.0)
    clock.now = 20.0
    reg.mark(("qwen", "ep1"), 30.0)  # fresh 30s from t=20 → expires at 50
    clock.now = 49.9
    assert reg.is_cooling(("qwen", "ep1")) is True
