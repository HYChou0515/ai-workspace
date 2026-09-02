"""Rate-limit-aware retry (429) — wait what the provider ASKED us to wait.

429 is the one transient whose recovery is defined by *waiting*: the endpoint
is not broken and switching away does not help (on a single-endpoint deploy
there is nowhere to switch anyway). The provider usually states how long in the
response, so these cover reading that figure back out of a real litellm error.

The double is litellm's OWN exception class, built the way litellm builds it —
a hand-rolled stand-in would let the extractor and the test agree on a shape
the provider never sends.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import litellm
import pytest

from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.failover.core import (
    AllProvidersFailed,
    CallProvider,
    Provider,
    failover_call,
    failover_stream,
)
from workspace_app.failover.rate_limit import is_rate_limited, retry_after_s
from workspace_app.failover.retry import try_provider


def _recording_sleep() -> tuple[list[float], Callable[[float], None]]:
    slept: list[float] = []
    return slept, slept.append


def _rate_limited(headers: dict[str, str]) -> litellm.exceptions.RateLimitError:
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


def test_retry_after_reads_the_seconds_the_429_states():
    assert retry_after_s(_rate_limited({"retry-after": "7"})) == 7.0


def test_retry_after_is_none_when_the_provider_did_not_say():
    assert retry_after_s(_rate_limited({})) is None


def test_retry_after_accepts_the_http_date_form():
    """RFC 7231 allows an absolute date as well as delay-seconds, and a gateway
    or CDN in front of the model sends that form. The clock is injected so the
    suite is not timing-dependent."""
    exc = _rate_limited({"retry-after": "Wed, 21 Oct 2015 07:28:30 GMT"})
    at_7_28_00 = 1445412480.0
    assert retry_after_s(exc, now=lambda: at_7_28_00) == 30.0


def test_retry_after_never_reports_a_wait_in_the_past():
    """Our clock and the gateway's differ, and the response may have queued —
    so the stated moment can already be behind us. Zero means "go now"; a
    negative figure would either throw in ``sleep`` or read as a wait."""
    exc = _rate_limited({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
    a_minute_later = 1445412540.0
    assert retry_after_s(exc, now=lambda: a_minute_later) == 0.0


def test_retry_after_survives_a_header_it_cannot_parse():
    """This runs inside an ``except`` block. An extractor that raises turns a
    recoverable rate limit into a hard failure, so an unreadable value must
    read as "the provider didn't say" and let the caller back off instead."""
    assert retry_after_s(_rate_limited({"retry-after": "soon"})) is None


def _unavailable() -> litellm.exceptions.ServiceUnavailableError:
    return litellm.exceptions.ServiceUnavailableError(
        message="upstream is restarting",
        llm_provider="openai",
        model="gpt",
        response=httpx.Response(503, request=httpx.Request("POST", "http://x/v1/chat/completions")),
    )


def test_a_429_is_recognised_as_rate_limiting():
    assert is_rate_limited(_rate_limited({})) is True


def test_a_gateway_blip_is_not_rate_limiting():
    """503 is transient too, but the endpoint is genuinely unwell — switching
    away helps and waiting in place does not. Only 429 takes the wait path."""
    assert is_rate_limited(_unavailable()) is False


def _wrapped(inner: BaseException) -> litellm.exceptions.APIConnectionError:
    """The 429 as the agent turn actually receives it: litellm re-raises the
    cause wrapped in an APIConnectionError, so the status lands one link down
    the ``__cause__`` chain."""
    try:
        raise inner
    except Exception as cause:
        try:
            raise litellm.exceptions.APIConnectionError(
                message="APIConnectionError: litellm.RateLimitError",
                llm_provider="openai",
                model="gpt",
            ) from cause
        except litellm.exceptions.APIConnectionError as outer:
            return outer


def test_a_429_wrapped_by_the_sdk_is_still_recognised():
    """Reading only the outermost exception would silently disable the whole
    wait path in exactly the turn this feature exists to fix."""
    assert is_rate_limited(_wrapped(_rate_limited({"retry-after": "3"}))) is True


def test_the_stated_wait_is_found_through_the_wrapper_too():
    """The wrapper carries no headers of its own. Knowing it is a rate limit
    but not for how long would leave us guessing at the one number the
    provider actually told us."""
    assert retry_after_s(_wrapped(_rate_limited({"retry-after": "3"}))) == 3.0


# ── the same-endpoint retry actually waits ────────────────────────────────────


def test_a_rate_limited_retry_waits_the_duration_the_provider_stated():
    """Retrying a 429 after the ordinary 0.2s gap just spends an attempt to be
    told "too fast" again. The provider said how long; wait that long."""
    slept, sleep = _recording_sleep()
    calls = {"n": 0}

    def call():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _rate_limited({"retry-after": "7"})
        return "ok"

    assert try_provider(call, m=5, gap=0.2, sleep=sleep) == "ok"
    assert calls["n"] == 2
    assert slept == [7.0]  # the stated wait, not the fixed gap


def test_a_rate_limit_that_states_nothing_backs_off_instead_of_using_the_gap():
    """ "Retry-After" is optional, so plenty of 429s say only "too fast". The
    gap is tuned for a gateway blip — hammering a rate limiter every 0.2s is
    what got us throttled. Fall back to the same doubling shape the chain
    re-sweep uses (`failover.round_backoff_s`)."""
    slept, sleep = _recording_sleep()

    def call():
        raise _rate_limited({})  # 429 with no Retry-After

    with pytest.raises(litellm.exceptions.RateLimitError):
        try_provider(call, m=4, gap=0.2, sleep=sleep)

    assert slept == [1.0, 2.0, 4.0]


# ── the chain must not walk away from a healthy endpoint ──────────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _refusing(exc: BaseException) -> Callable[[], Iterator[str]]:
    def start() -> Iterator[str]:
        raise exc
        yield ""  # pragma: no cover — unreachable; makes this a generator

    return start


def _one(start: Callable[[], Iterator[str]], *, num_retries: int = 0) -> Provider[str]:
    return Provider(
        key="a",
        label="a",
        start=start,
        ttft_s=5.0,
        idle_s=5.0,
        cooldown_s=30.0,
        num_retries=num_retries,
    )


def test_a_rate_limited_provider_is_parked_only_as_long_as_it_asked():
    """``cooldown_s`` is the bench time for an endpoint that is *unwell*. A
    rate-limited one is healthy and serves again the moment its window rolls,
    so benching it the full 30s keeps us off a working endpoint far longer
    than it asked for.

    Parking it for exactly the stated wait says the same thing in the
    mechanism that already exists — and because the registry is keyed by
    ``(model, endpoint)`` and shared across roles, every other caller then
    respects the same window instead of rediscovering it one 429 at a time."""
    clock = _Clock()
    registry = CooldownRegistry(clock=clock)
    _, sleep = _recording_sleep()

    with pytest.raises(AllProvidersFailed):
        list(
            failover_stream(
                [_one(_refusing(_rate_limited({"retry-after": "1"})))],
                registry,
                sleep=sleep,
            )
        )

    assert registry.is_cooling("a") is True  # the window it asked for
    clock.now = 1.5
    assert registry.is_cooling("a") is False  # a 30s bench would still hold


def test_a_rate_limit_that_states_nothing_falls_back_to_the_normal_bench():
    """With no window stated there is no better number than the configured
    ``cooldown_s``. Parking for the short same-endpoint backoff instead would
    put the chain straight back onto an endpoint we know is throttling."""
    clock = _Clock()
    registry = CooldownRegistry(clock=clock)
    _, sleep = _recording_sleep()

    with pytest.raises(AllProvidersFailed):
        list(
            failover_stream(
                [_one(_refusing(_rate_limited({})))],  # 429, no Retry-After
                registry,
                sleep=sleep,
            )
        )

    clock.now = 29.0
    assert registry.is_cooling("a") is True  # the full cooldown_s, not a guess
    clock.now = 31.0
    assert registry.is_cooling("a") is False


def test_same_endpoint_retries_wait_the_stated_window_between_attempts():
    """The same-endpoint retries exist to absorb a blip, so they fire back to
    back. Against a rate limiter that is three refusals in a row inside a
    millisecond — the retry budget is spent before the window could possibly
    have rolled. Retrying in the same place only means anything if it waits."""
    slept, sleep = _recording_sleep()

    with pytest.raises(AllProvidersFailed):
        list(
            failover_stream(
                [_one(_refusing(_rate_limited({"retry-after": "3"})), num_retries=2)],
                CooldownRegistry(clock=_Clock()),
                sleep=sleep,
            )
        )

    assert slept == [3.0, 3.0]  # between the three attempts at the same endpoint


def test_the_non_streaming_chain_holds_the_same_way():
    """`failover_call` is the same loop for requests that return in one piece
    (embeddings, VLM describes). It benched a rate-limited endpoint for the
    full `cooldown_s` and re-called it with no pause, exactly as the streaming
    one did — the fix has to land on both or the defect just moves."""
    clock = _Clock()
    registry = CooldownRegistry(clock=clock)
    slept, sleep = _recording_sleep()

    def call():
        raise _rate_limited({"retry-after": "2"})

    with pytest.raises(AllProvidersFailed):
        failover_call(
            [CallProvider(key="a", label="a", call=call, cooldown_s=30.0, num_retries=1)],
            registry,
            sleep=sleep,
        )

    assert slept == [2.0]  # held between the two same-endpoint attempts
    clock.now = 2.5
    assert registry.is_cooling("a") is False  # parked 2s, not 30s


# ── the floor: no rate-limit wait is ever shorter than the doubling backoff ──
# A stated 0 (or 0.001) honoured literally re-sends at the cadence that earned
# the throttle, and a TIME budget never spends against zero-length waits — the
# hole both the turn loop and the agent chain would otherwise share.


def test_rate_limit_wait_is_floored_at_the_backoff():
    from workspace_app.failover.rate_limit import rate_limit_wait_s

    assert rate_limit_wait_s(_rate_limited({"retry-after": "0"}), attempt=1) == 1.0
    assert rate_limit_wait_s(_rate_limited({"retry-after": "0.001"}), attempt=3) == 4.0
    # …and the floor never CAPS a real window down.
    assert rate_limit_wait_s(_rate_limited({"retry-after": "90"}), attempt=1) == 90.0
    # No stated window at all keeps the plain backoff.
    assert rate_limit_wait_s(_rate_limited({}), attempt=2) == 2.0
