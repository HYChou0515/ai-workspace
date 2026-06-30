"""Transient-error retry for index-time model calls (#249).

A single Ollama endpoint has nowhere to fail over to, so a transient gateway
blip (litellm.BadGatewayError / 502, a connection reset, a timeout) used to
kill the whole index job. These cover the in-process retry that absorbs the
blip: ``try_provider`` gives one provider a few quick attempts; everything is
classified through ``is_transient`` so a real bug (400, KeyError) never loops.

Sleeping is injected so the suite never actually waits.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from workspace_app.failover.core import CallProvider
from workspace_app.failover.retry import call_with_failover, is_transient, try_provider


def _prov(label: str, call) -> CallProvider:
    return CallProvider(key=label, label=label, call=call, cooldown_s=0.0)


def _fails(exc: BaseException):
    def call():
        raise exc

    return call


class _Status(Exception):
    """A fake provider error carrying an HTTP status_code, like litellm's."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _recording_sleep() -> tuple[list[float], Callable[[float], None]]:
    slept: list[float] = []
    return slept, slept.append


def test_try_provider_retries_a_transient_failure_then_returns():
    slept, sleep = _recording_sleep()
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Status(502)  # first attempt: transient blip
        return "ok"

    out = try_provider(call, m=5, gap=0.2, sleep=sleep)

    assert out == "ok"
    assert calls["n"] == 2  # retried once
    assert slept == [0.2]  # exactly one 0.2s gap between the two attempts


def test_try_provider_does_not_retry_a_permanent_error():
    slept, sleep = _recording_sleep()
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        raise _Status(400)  # a bad request can never succeed on retry

    with pytest.raises(_Status):
        try_provider(call, m=5, gap=0.2, sleep=sleep)

    assert calls["n"] == 1  # tried exactly once, no retry
    assert slept == []


def test_try_provider_gives_up_after_m_transient_attempts():
    slept, sleep = _recording_sleep()
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        raise _Status(503)  # always transient → exhausts the budget

    with pytest.raises(_Status):
        try_provider(call, m=5, gap=0.2, sleep=sleep)

    assert calls["n"] == 5  # m attempts
    assert slept == [0.2, 0.2, 0.2, 0.2]  # m-1 gaps between them


@pytest.mark.parametrize("code", [408, 409, 429, 500, 502, 503, 504])
def test_is_transient_true_for_retryable_http_statuses(code):
    assert is_transient(_Status(code)) is True


def test_is_transient_true_for_connection_and_timeout():
    assert is_transient(TimeoutError("slow")) is True
    assert is_transient(ConnectionError("reset")) is True


@pytest.mark.parametrize("code", [400, 401, 404, 422])
def test_is_transient_false_for_client_errors(code):
    assert is_transient(_Status(code)) is False


def test_is_transient_false_for_a_plain_bug():
    assert is_transient(KeyError("boom")) is False


def test_call_with_failover_switches_to_next_provider_on_transient():
    slept, sleep = _recording_sleep()
    switched: list[str] = []

    out = call_with_failover(
        [_prov("a", _fails(_Status(502))), _prov("b", lambda: "from-b")],
        m=1,  # one shot per provider so we cross to 'b' immediately
        round_delays=(),  # single sweep — 'b' wins it, no re-sweep needed
        sleep=sleep,
        on_switch=lambda p, exc: switched.append(p.label),
    )

    assert out == "from-b"
    assert switched == ["a"]  # 'a' failed transiently → switched off it
    assert slept == []  # 'b' succeeded within the first sweep — no round backoff


def test_call_with_failover_aborts_immediately_on_a_permanent_error():
    switched: list[str] = []
    b_called = {"n": 0}

    def b():
        b_called["n"] += 1  # pragma: no cover — must never run
        return "from-b"

    with pytest.raises(_Status):
        call_with_failover(
            [_prov("a", _fails(_Status(400))), _prov("b", b)],
            m=1,
            round_delays=(),
            on_switch=lambda p, exc: switched.append(p.label),
        )

    assert switched == []  # a permanent error is not a switch
    assert b_called["n"] == 0  # the chain is abandoned, not walked


def test_call_with_failover_backs_off_and_re_sweeps_until_a_round_succeeds():
    slept, sleep = _recording_sleep()
    switched: list[str] = []
    rounds = {"a": 0}

    def a():
        rounds["a"] += 1
        if rounds["a"] == 1:
            raise _Status(502)  # fails the first sweep, succeeds the second
        return "from-a"

    out = call_with_failover(
        [_prov("a", a), _prov("b", _fails(_Status(503)))],
        m=1,
        round_delays=(1.0, 2.0),
        sleep=sleep,
        on_switch=lambda p, exc: switched.append(p.label),
    )

    assert out == "from-a"
    assert slept == [1.0]  # one round backoff between the two sweeps
    assert switched == ["a", "b"]  # both failed in sweep 1; 'a' won sweep 2


def test_call_with_failover_raises_the_last_transient_when_all_rounds_exhaust():
    slept, sleep = _recording_sleep()

    with pytest.raises(_Status) as ei:
        call_with_failover(
            [_prov("a", _fails(_Status(502))), _prov("b", _fails(_Status(503)))],
            m=1,
            round_delays=(0.0,),  # two sweeps, then give up
            sleep=sleep,
        )

    assert ei.value.status_code == 503  # the last provider's transient propagates
    assert slept == [0.0]  # one backoff between the two exhausted sweeps
