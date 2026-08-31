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

import httpx
import litellm

from workspace_app.failover.rate_limit import is_rate_limited, retry_after_s


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
