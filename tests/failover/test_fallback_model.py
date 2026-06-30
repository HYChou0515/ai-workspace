"""FallbackModel — async busy-aware failover for the agent (SDK Model) path.

Driven with fake SDK models (an async get_response + an async-generator
stream_response) so the failover policy is exercised without a network.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agents.models.interface import Model

from workspace_app.factories import LlmEndpoint
from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.failover.core import AllProvidersFailed
from workspace_app.failover.model import FallbackModel


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _ep(
    model: str,
    *,
    ttft_s: float = 5.0,
    idle_s: float = 5.0,
    cooldown_s: float = 30.0,
    num_retries: int = 0,
    round_backoff_s: tuple[float, ...] = (),
    total_deadline_s: float = float("inf"),
) -> LlmEndpoint:
    return LlmEndpoint(
        model=model,
        base_url=None,
        api_key=None,
        reasoning_effort=None,
        ttft_s=ttft_s,
        idle_s=idle_s,
        cooldown_s=cooldown_s,
        num_retries=num_retries,
        round_backoff_s=round_backoff_s,
        total_deadline_s=total_deadline_s,
    )


class _FakeModel(Model):
    """A stand-in SDK model: get_response returns/raises; stream_response yields
    the given events, optionally erroring or stalling at a chosen position."""

    def __init__(
        self, events=None, *, response=None, error=None, stall_after=None, stall_event=None
    ):
        self._events = events or []
        self._response = response
        self._error = error
        self._stall_after = stall_after
        self._stall_event = stall_event

    async def get_response(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._response

    async def stream_response(self, *args, **kwargs) -> AsyncIterator[Any]:
        if self._error is not None:
            raise self._error
        for i, ev in enumerate(self._events):
            if self._stall_after is not None and i == self._stall_after:
                assert self._stall_event is not None
                await self._stall_event.wait()
            yield ev


class _MidFailModel(Model):
    """Yields one event then raises — a mid-stream (post-first) failure."""

    async def get_response(self, *args, **kwargs):  # pragma: no cover — unused
        raise NotImplementedError

    async def stream_response(self, *args, **kwargs) -> AsyncIterator[Any]:
        yield "partial"
        raise RuntimeError("mid")


def _model(reg, impls: dict[str, _FakeModel], **kw) -> FallbackModel:
    return FallbackModel(list(kw.pop("endpoints")), reg, make_model=lambda e: impls[e.model], **kw)


async def _collect(agen) -> list:
    return [ev async for ev in agen]


def test_stream_switches_on_pre_first_error():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        impls = {"busy": _FakeModel(error=RuntimeError("500")), "spare": _FakeModel(["a", "b"])}
        switched: list[str] = []
        m = _model(
            reg,
            impls,
            endpoints=[_ep("busy"), _ep("spare")],
            on_switch=lambda label, exc: switched.append(label),
        )
        out = await _collect(m.stream_response())
        assert out == ["a", "b"]
        assert switched == ["busy"]
        assert reg.is_cooling(("busy", "")) is True

    asyncio.run(run())


def test_stream_ttft_timeout_switches():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        ev = asyncio.Event()
        # 'slow' stalls BEFORE its first event (stall_after=0); ttft is tiny.
        impls = {
            "slow": _FakeModel(["x"], stall_after=0, stall_event=ev),
            "fast": _FakeModel(["quick"]),
        }
        m = _model(reg, impls, endpoints=[_ep("slow", ttft_s=0.05), _ep("fast")])
        out = await _collect(m.stream_response())
        assert out == ["quick"]
        assert reg.is_cooling(("slow", "")) is True
        ev.set()

    asyncio.run(run())


def test_stream_failure_after_first_event_propagates():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        m = FallbackModel([_ep("a"), _ep("b")], reg, make_model=lambda e: _MidFailModel())
        with pytest.raises(RuntimeError, match="mid"):
            await _collect(m.stream_response())
        assert reg.is_cooling(("a", "")) is False  # produced output ⇒ not busy

    asyncio.run(run())


def test_stream_all_fail_raises():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        impls = {"a": _FakeModel(error=RuntimeError()), "b": _FakeModel(error=RuntimeError())}
        m = _model(reg, impls, endpoints=[_ep("a"), _ep("b")])
        with pytest.raises(AllProvidersFailed):
            await _collect(m.stream_response())

    asyncio.run(run())


def test_stream_empty_turn_is_success():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        impls = {"a": _FakeModel([]), "b": _FakeModel(["fallback"])}
        m = _model(reg, impls, endpoints=[_ep("a"), _ep("b")])
        assert await _collect(m.stream_response()) == []

    asyncio.run(run())


def test_stream_skips_cooling_endpoint():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        reg.mark(("busy", ""), 30.0)
        built: list[str] = []

        def make(e):
            built.append(e.model)
            return _FakeModel(["x"]) if e.model == "spare" else _FakeModel(error=RuntimeError())

        m = FallbackModel([_ep("busy"), _ep("spare")], reg, make_model=make)
        assert await _collect(m.stream_response()) == ["x"]
        assert built == ["spare"]  # cooling 'busy' never materialised

    asyncio.run(run())


def test_get_response_switches_on_error():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        impls = {"busy": _FakeModel(error=RuntimeError("500")), "spare": _FakeModel(response="ok")}
        m = _model(reg, impls, endpoints=[_ep("busy"), _ep("spare")])
        assert await m.get_response() == "ok"
        assert reg.is_cooling(("busy", "")) is True

    asyncio.run(run())


def test_get_response_skips_cooling_and_exhausts():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        reg.mark(("a", ""), 30.0)
        impls = {"a": _FakeModel(response="never"), "b": _FakeModel(error=RuntimeError())}
        m = _model(reg, impls, endpoints=[_ep("a"), _ep("b")])
        with pytest.raises(AllProvidersFailed):
            await m.get_response()

    asyncio.run(run())


# ── #196-followup: configurable resilience on the async agent path ──


class _Recovering(Model):
    """Fails its first ``stream_response`` (or ``get_response``) attempt before any
    event, then succeeds — to exercise same-endpoint retry / re-sweep recovery."""

    def __init__(self, *, fail_first: int = 1) -> None:
        self._stream_calls = 0
        self._resp_calls = 0
        self._fail_first = fail_first

    async def get_response(self, *args, **kwargs):
        self._resp_calls += 1
        if self._resp_calls <= self._fail_first:
            raise RuntimeError("busy")
        return "recovered"

    async def stream_response(self, *args, **kwargs) -> AsyncIterator[Any]:
        self._stream_calls += 1
        if self._stream_calls <= self._fail_first:
            raise RuntimeError("busy")
        yield "recovered"


def _advancing_async_sleep(clock: _Clock):
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)
        clock.now += s

    return slept, sleep


def test_stream_re_sweeps_after_cooldown_clears():
    async def run():
        clock = _Clock()
        reg = CooldownRegistry(clock=clock)
        slept, sleep = _advancing_async_sleep(clock)
        impl = _Recovering()
        m = FallbackModel(
            [_ep("a", cooldown_s=30.0, round_backoff_s=(1.0,))],
            reg,
            make_model=lambda e: impl,
            sleep=sleep,
        )
        out = await _collect(m.stream_response())
        assert out == ["recovered"]
        assert slept == [30.0]  # waited out the cooldown before re-sweeping

    asyncio.run(run())


def test_stream_num_retries_recovers_on_same_endpoint():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        impl = _Recovering()
        switched: list[str] = []
        m = FallbackModel(
            [_ep("a", num_retries=1)],
            reg,
            make_model=lambda e: impl,
            on_switch=lambda label, exc: switched.append(label),
        )
        out = await _collect(m.stream_response())
        assert out == ["recovered"]
        assert switched == []  # recovered on the same endpoint → no switch
        assert reg.is_cooling(("a", "")) is False

    asyncio.run(run())


def test_get_response_re_sweeps_until_recovered():
    async def run():
        clock = _Clock()
        reg = CooldownRegistry(clock=clock)
        slept, sleep = _advancing_async_sleep(clock)
        impl = _Recovering()
        m = FallbackModel(
            [_ep("a", cooldown_s=5.0, round_backoff_s=(1.0,))],
            reg,
            make_model=lambda e: impl,
            sleep=sleep,
        )
        assert await m.get_response() == "recovered"
        assert slept == [5.0]

    asyncio.run(run())


def test_stream_total_deadline_gives_up_readably():
    async def run():
        clock = _Clock()
        reg = CooldownRegistry(clock=clock)
        slept, sleep = _advancing_async_sleep(clock)
        impls = {"a": _FakeModel(error=RuntimeError("x"))}
        m = FallbackModel(
            [_ep("a", cooldown_s=30.0, round_backoff_s=(1.0, 1.0, 1.0), total_deadline_s=50.0)],
            reg,
            make_model=lambda e: impls[e.model],
            sleep=sleep,
        )
        with pytest.raises(AllProvidersFailed):
            await _collect(m.stream_response())
        assert slept == [30.0, 20.0]  # never sleeps past the 50s deadline

    asyncio.run(run())


def test_stream_re_sweep_with_zero_cooldown_does_not_sleep():
    async def run():
        clock = _Clock()
        reg = CooldownRegistry(clock=clock)
        slept, sleep = _advancing_async_sleep(clock)
        impl = _Recovering()
        m = FallbackModel(
            [_ep("a", cooldown_s=0.0, round_backoff_s=(0.0,))],
            reg,
            make_model=lambda e: impl,
            sleep=sleep,
        )
        out = await _collect(m.stream_response())
        assert out == ["recovered"]
        assert slept == []  # backoff 0 + nothing cooling ⇒ wait 0 ⇒ no sleep

    asyncio.run(run())


def test_get_response_num_retries_recovers_on_same_endpoint():
    async def run():
        reg = CooldownRegistry(clock=_Clock())
        impl = _Recovering()
        switched: list[str] = []
        m = FallbackModel(
            [_ep("a", num_retries=1)],
            reg,
            make_model=lambda e: impl,
            on_switch=lambda label, exc: switched.append(label),
        )
        assert await m.get_response() == "recovered"
        assert switched == []  # recovered on the same endpoint → no switch
        assert reg.is_cooling(("a", "")) is False

    asyncio.run(run())


def test_get_response_total_deadline_gives_up():
    async def run():
        clock = _Clock()
        reg = CooldownRegistry(clock=clock)
        slept, sleep = _advancing_async_sleep(clock)
        impls = {"a": _FakeModel(error=RuntimeError("x"))}
        m = FallbackModel(
            [_ep("a", cooldown_s=30.0, round_backoff_s=(1.0,), total_deadline_s=0.0)],
            reg,
            make_model=lambda e: impls[e.model],
            sleep=sleep,
        )
        with pytest.raises(AllProvidersFailed):
            await m.get_response()
        assert slept == []

    asyncio.run(run())
