"""failover_stream — the strict-priority, busy-aware switching loop.

Behaviour under test (all through the public driver, with fake providers):
- a provider that fails BEFORE its first item ⇒ switch to the next;
- a provider that succeeds ⇒ its items are yielded, no switch, no cooldown;
- a failed provider is parked on cooldown; a cooling provider is skipped;
- no first item within ``ttft_s`` ⇒ treated as a pre-first failure (busy) ⇒ switch;
- a failure AFTER the first item ⇒ propagate (a stream already seen can't restart);
- every provider exhausted ⇒ AllProvidersFailed.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.failover.core import (
    AllProvidersFailed,
    Provider,
    StreamStalled,
    failover_stream,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _reg(clock: _Clock | None = None) -> CooldownRegistry:
    return CooldownRegistry(clock=clock or _Clock())


def _prov(key: str, start, *, ttft_s: float = 5.0, idle_s: float = 5.0, cooldown_s: float = 30.0):
    return Provider(
        key=key, label=key, start=start, ttft_s=ttft_s, idle_s=idle_s, cooldown_s=cooldown_s
    )


def _yield(*items: str):
    def start() -> Iterator[str]:
        yield from items

    return start


def _raise(exc: BaseException):
    def start() -> Iterator[str]:
        raise exc
        yield  # pragma: no cover — unreachable, marks this a generator

    return start


def _block(ev: threading.Event):
    def start() -> Iterator[str]:
        ev.wait()  # never yields until released
        return
        yield  # pragma: no cover

    return start


def _mid_fail(first: str, exc: BaseException):
    def start() -> Iterator[str]:
        yield first
        raise exc

    return start


def _yield_then_block(first: str, ev: threading.Event):
    def start() -> Iterator[str]:
        yield first
        ev.wait()  # goes silent after the first item → idle stall
        return
        yield  # pragma: no cover

    return start


def test_switches_to_next_provider_when_first_fails_pre_first():
    switched: list[tuple[str, str]] = []
    out = list(
        failover_stream(
            [_prov("a", _raise(RuntimeError("busy"))), _prov("b", _yield("hello"))],
            _reg(),
            on_switch=lambda p, exc: switched.append((p.key, str(exc))),
        )
    )
    assert out == ["hello"]
    assert switched == [("a", "busy")]  # exactly one switch, off the failed provider


def test_success_on_first_provider_yields_without_switch_or_cooldown():
    clock = _Clock()
    reg = _reg(clock)
    switched: list[str] = []
    out = list(
        failover_stream(
            [_prov("a", _yield("x", "y")), _prov("b", _yield("z"))],
            reg,
            on_switch=lambda p, exc: switched.append(p.key),
        )
    )
    assert out == ["x", "y"]
    assert switched == []
    assert reg.is_cooling("a") is False  # winner is not parked


def test_failed_provider_is_put_on_cooldown():
    clock = _Clock()
    reg = _reg(clock)
    list(
        failover_stream(
            [_prov("a", _raise(RuntimeError()), cooldown_s=30.0), _prov("b", _yield("ok"))], reg
        )
    )
    assert reg.is_cooling("a") is True
    clock.now = 30.0
    assert reg.is_cooling("a") is False


def test_cooling_provider_is_skipped_entirely():
    clock = _Clock()
    reg = _reg(clock)
    reg.mark("a", 30.0)
    started: list[str] = []

    def watched_start():
        started.append("a")
        yield "from-a"  # pragma: no cover — must never run

    out = list(failover_stream([_prov("a", watched_start), _prov("b", _yield("from-b"))], reg))
    assert out == ["from-b"]
    assert started == []  # 'a' is cooling → never even started


def test_ttft_timeout_counts_as_pre_first_failure_and_switches():
    ev = threading.Event()
    try:
        out = list(
            failover_stream(
                [_prov("slow", _block(ev), ttft_s=0.05), _prov("fast", _yield("quick"))],
                _reg(),
            )
        )
        assert out == ["quick"]  # 'slow' never produced a token in time ⇒ switched
    finally:
        ev.set()  # release the parked producer thread


def test_failure_after_first_item_propagates_and_does_not_switch():
    reg = _reg()
    switched: list[str] = []
    with pytest.raises(RuntimeError, match="mid"):
        list(
            failover_stream(
                [
                    _prov("a", _mid_fail("partial", RuntimeError("mid"))),
                    _prov("b", _yield("never")),
                ],
                reg,
                on_switch=lambda p, exc: switched.append(p.key),
            )
        )
    assert switched == []  # mid-stream failure is terminal, not a switch
    assert reg.is_cooling("a") is False  # produced output ⇒ not "busy"


def test_all_providers_failing_raises_all_providers_failed():
    with pytest.raises(AllProvidersFailed):
        list(
            failover_stream(
                [_prov("a", _raise(RuntimeError("x"))), _prov("b", _raise(RuntimeError("y")))],
                _reg(),
            )
        )


def test_mid_stream_stall_raises_and_does_not_switch():
    ev = threading.Event()
    switched: list[str] = []
    try:
        with pytest.raises(StreamStalled):
            list(
                failover_stream(
                    [
                        _prov("a", _yield_then_block("partial", ev), idle_s=0.05),
                        _prov("b", _yield("never")),
                    ],
                    _reg(),
                    on_switch=lambda p, exc: switched.append(p.key),
                )
            )
        assert switched == []  # already produced output ⇒ terminal, not a switch
    finally:
        ev.set()


def test_empty_stream_is_a_success_not_a_switch():
    switched: list[str] = []
    out = list(
        failover_stream(
            [_prov("a", _yield()), _prov("b", _yield("fallback"))],
            _reg(),
            on_switch=lambda p, exc: switched.append(p.key),
        )
    )
    assert out == []  # empty completion is valid per the ILlm contract
    assert switched == []
