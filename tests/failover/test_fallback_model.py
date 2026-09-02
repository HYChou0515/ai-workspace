"""FallbackModel — async busy-aware failover for the agent (SDK Model) path.

Driven with fake SDK models (an async get_response + an async-generator
stream_response) so the failover policy is exercised without a network.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import litellm
import pytest
from agents.models.interface import Model

from workspace_app.factories import LlmEndpoint
from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.failover.core import AllProvidersFailed
from workspace_app.failover.model import FallbackModel
from workspace_app.failover.rate_limit import is_rate_limited


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


# ── #748: which endpoint actually answered ───────────────────────────────────


async def test_the_chain_remembers_which_endpoint_actually_answered():
    """Nothing recorded this, so the only way to name a turn's model was to read
    the CONFIGURED one — which is right until failover makes it wrong, i.e. it
    is wrong in precisely the case the question is worth asking. The #69 trace
    has been reporting the chain head this whole time."""
    registry = CooldownRegistry(clock=_Clock())
    chain = FallbackModel(
        [_ep("primary"), _ep("backup")],
        registry,
        make_model=lambda e: (
            _FakeModel(error=RuntimeError("busy"))
            if e.model == "primary"
            else _FakeModel(response="ok")
        ),
    )

    assert await chain.get_response() == "ok"
    assert chain.served_model == "backup"  # not "primary", the configured head


async def test_the_streamed_path_remembers_it_too():
    registry = CooldownRegistry(clock=_Clock())
    chain = FallbackModel(
        [_ep("primary"), _ep("backup")],
        registry,
        make_model=lambda e: (
            _FakeModel(error=RuntimeError("busy"))
            if e.model == "primary"
            else _FakeModel(events=["a"])
        ),
    )

    assert [ev async for ev in chain.stream_response()] == ["a"]
    assert chain.served_model == "backup"


async def test_nothing_is_claimed_before_anything_answers():
    """A chain that has not served yet must not name a model. Defaulting to the
    head would be the same lie, just earlier."""
    registry = CooldownRegistry(clock=_Clock())
    chain = FallbackModel(
        [_ep("primary")], registry, make_model=lambda e: _FakeModel(response="ok")
    )

    assert chain.served_model is None


# ── 429: wait it out at the SAME endpoint (#742's rule, applied to the chain) ─
#
# #742 taught three call paths that a rate limit is cured by WAITING, not by
# switching — and this chain was the fourth path, still doing the old thing:
# zero-wait same-endpoint retries, then parking the healthy endpoint for the
# fixed cooldown. On a rate-limited multi-endpoint deploy the chain burned every
# endpoint in milliseconds and surfaced AllProvidersFailed, whose message is the
# "giving up after N attempts: … all agent models failed or were cooling" the
# report keeps seeing.
#
# The doubles are litellm's OWN exception classes, built the way litellm builds
# them (same rationale as test_rate_limit.py): a hand-rolled 429 would let the
# detector and the test agree on a shape the provider never sends.


def _429(retry_after: str | None) -> litellm.exceptions.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return litellm.exceptions.RateLimitError(
        message="rate limit exceeded",
        llm_provider="openai",
        model="gpt",
        response=httpx.Response(
            429,
            headers=headers,
            request=httpx.Request("POST", "http://x/v1/chat/completions"),
        ),
    )


def _wrapped_429(retry_after: str | None) -> litellm.exceptions.APIConnectionError:
    """The form the agents SDK actually hands us: the 429 re-raised under an
    APIConnectionError, status and headers one link down the cause chain."""
    try:
        raise _429(retry_after)
    except litellm.exceptions.RateLimitError as inner:
        try:
            raise litellm.exceptions.APIConnectionError(
                message="APIConnectionError: litellm.RateLimitError",
                llm_provider="openai",
                model="gpt",
            ) from inner
        except litellm.exceptions.APIConnectionError as outer:
            return outer


class _FlakyModel(Model):
    """Raises the queued errors one per call, then serves."""

    def __init__(self, errors: list[BaseException], *, response=None, events=None) -> None:
        self._errors = list(errors)
        self._response = response
        self._events = events or []

    async def get_response(self, *args, **kwargs):
        if self._errors:
            raise self._errors.pop(0)
        return self._response

    async def stream_response(self, *args, **kwargs) -> AsyncIterator[Any]:
        if self._errors:
            raise self._errors.pop(0)
        for ev in self._events:
            yield ev


def _held_sleep(clock: _Clock) -> tuple[list[float], Any]:
    """An async sleep double that records and ADVANCES the fake clock, so a
    stated window interacts with the chain deadline the way real time would."""
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)
        clock.now += s

    return slept, sleep


async def test_get_response_waits_out_a_429_at_the_same_endpoint():
    """The window the provider stated, at the endpoint that stated it — no
    switch, no cooldown, and `num_retries` (0 here) untouched: that budget is
    for a broken endpoint, and this one is healthy."""
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    slept, sleep = _held_sleep(clock)
    built: list[str] = []
    # Stable instances: make_model is called per ATTEMPT, so a double built
    # fresh in the factory would re-arm its error queue forever.
    impls = {
        "primary": _FlakyModel([_429("7")], response="ok"),
        "backup": _FakeModel(response="never"),
    }

    def make(e):
        built.append(e.model)
        return impls[e.model]

    m = FallbackModel([_ep("primary"), _ep("backup")], reg, make_model=make, sleep=sleep)

    assert await m.get_response() == "ok"
    assert slept == [7.0]
    assert built.count("backup") == 0  # never switched
    assert reg.is_cooling(("primary", "")) is False  # healthy, not parked


async def test_stream_waits_out_a_wrapped_429_before_the_first_event():
    """Same rule on the streamed path, with the 429 in the wrapped form the SDK
    actually raises — the status sits under __cause__, not on the outer error."""
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    slept, sleep = _held_sleep(clock)
    impls = {
        "primary": _FlakyModel([_wrapped_429("3")], events=["a", "b"]),
        "backup": _FakeModel(["never"]),
    }
    m = FallbackModel(
        [_ep("primary"), _ep("backup")], reg, make_model=lambda e: impls[e.model], sleep=sleep
    )

    assert [ev async for ev in m.stream_response()] == ["a", "b"]
    assert slept == [3.0]
    assert reg.is_cooling(("primary", "")) is False


async def test_429_without_retry_after_backs_off_doubling():
    """A 429 that states no window still must not be re-sent at blip speed —
    same 1/2/4 shape the other paths use (`rate_limit.backoff_s`)."""
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    slept, sleep = _held_sleep(clock)
    flaky = _FlakyModel([_429(None), _429(None), _429(None)], response="ok")
    m = FallbackModel([_ep("primary")], reg, make_model=lambda e: flaky, sleep=sleep)

    assert await m.get_response() == "ok"
    assert slept == [1.0, 2.0, 4.0]


async def test_a_zero_retry_after_does_not_spin():
    """ "Retry-After: 0" honestly means "now" — but a provider that keeps saying
    it while still refusing would spin the hold loop at wire speed, with no time
    ever passing to spend the deadline. Repeated zero windows fall back to the
    doubling backoff instead."""
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    slept, sleep = _held_sleep(clock)
    flaky = _FlakyModel([_429("0"), _429("0")], response="ok")
    m = FallbackModel([_ep("primary")], reg, make_model=lambda e: flaky, sleep=sleep)

    assert await m.get_response() == "ok"
    assert slept == [1.0, 2.0]


async def test_a_window_past_the_deadline_parks_the_stated_window_and_moves_on():
    """When the stated wait cannot fit in the chain's remaining deadline, the
    endpoint is parked for THE WINDOW IT STATED — not the fixed cooldown — so
    every other caller sharing the registry honours it, and the chain tries the
    next endpoint instead of sleeping past its own budget."""
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    slept, sleep = _held_sleep(clock)
    impls = {
        "limited": _FlakyModel([_429("90")], response="never"),
        "backup": _FakeModel(response="ok"),
    }
    m = FallbackModel(
        [_ep("limited", total_deadline_s=10.0), _ep("backup")],
        reg,
        make_model=lambda e: impls[e.model],
        sleep=sleep,
    )

    assert await m.get_response() == "ok"
    assert slept == []  # never slept a window it could not afford
    assert reg.is_cooling(("limited", "")) is True
    assert reg.remaining([("limited", "")]) == pytest.approx(90.0)


async def test_an_exhausted_chain_still_names_the_rate_limit():
    """When every endpoint is limited past the deadline, the raised error must
    carry a 429 in its cause chain — the turn loop upstream distinguishes
    "rate limited" from "broken" by exactly that, and picking the wrong one
    turns a recoverable wait into "giving up after N attempts"."""
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    _slept, sleep = _held_sleep(clock)
    impls = {
        "a": _FlakyModel([_429("90")], response="never"),
        "b": _FlakyModel([RuntimeError("down")], response="never"),
    }
    m = FallbackModel(
        [_ep("a", total_deadline_s=10.0), _ep("b", total_deadline_s=10.0)],
        reg,
        make_model=lambda e: impls[e.model],
        sleep=sleep,
    )

    with pytest.raises(AllProvidersFailed) as err:
        await m.get_response()
    # "b" failed LAST, but the diagnosis worth surfacing is the rate limit.
    assert is_rate_limited(err.value)


async def test_streamed_exhaustion_names_the_rate_limit_too():
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    _slept, sleep = _held_sleep(clock)
    impls = {
        "a": _FlakyModel([_429("90")]),
        "b": _FlakyModel([RuntimeError("down")]),
    }
    m = FallbackModel(
        [_ep("a", total_deadline_s=10.0), _ep("b", total_deadline_s=10.0)],
        reg,
        make_model=lambda e: impls[e.model],
        sleep=sleep,
    )

    with pytest.raises(AllProvidersFailed) as err:
        _ = [ev async for ev in m.stream_response()]
    assert is_rate_limited(err.value)
