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


def test_now_exposes_the_injected_clock():
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    assert reg.now() == 0.0
    clock.now = 7.5
    assert reg.now() == 7.5


def test_remaining_is_zero_when_nothing_is_cooling():
    reg = CooldownRegistry(clock=_Clock())
    # No keys → nothing to wait for; unmarked keys are available now.
    assert reg.remaining([]) == 0.0
    assert reg.remaining([("qwen", "ep1"), ("qwen", "ep2")]) == 0.0


def test_remaining_is_zero_when_any_key_is_available_now():
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    reg.mark(("qwen", "ep1"), 30.0)  # ep1 cooling, ep2 free → a free one exists now
    assert reg.remaining([("qwen", "ep1"), ("qwen", "ep2")]) == 0.0


def test_remaining_is_soonest_expiry_when_all_keys_are_cooling():
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    reg.mark(("qwen", "ep1"), 30.0)
    reg.mark(("qwen", "ep2"), 12.0)  # ep2 frees first
    clock.now = 2.0
    assert reg.remaining([("qwen", "ep1"), ("qwen", "ep2")]) == 10.0  # 12 - 2


def test_remaining_treats_an_expired_key_as_available():
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    reg.mark(("qwen", "ep1"), 30.0)
    clock.now = 30.0  # ep1's deadline reached → no longer cooling
    assert reg.remaining([("qwen", "ep1")]) == 0.0


def test_remaining_keeps_the_earliest_when_a_later_key_cools_longer():
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    reg.mark(("qwen", "ep1"), 10.0)  # frees first
    reg.mark(("qwen", "ep2"), 30.0)  # cools longer → must NOT replace the soonest
    assert reg.remaining([("qwen", "ep1"), ("qwen", "ep2")]) == 10.0
