"""docs/plan-friendly-errors.md P1 — every failure a turn can end on is named by
ONE of a fixed set of kinds, so the FE can word it (D5) and the raw text can go
under technical details (D1) instead of being the whole message."""

from __future__ import annotations

import httpx
import litellm
import pytest

from workspace_app.failover.classify import ERROR_KINDS, classify_error
from workspace_app.failover.core import AllProvidersFailed


def _resp(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "http://llm.test/v1"))


def _wrapped(inner: BaseException) -> BaseException:
    """litellm / the agents SDK re-raise the real cause one link down."""
    try:
        try:
            raise inner
        except BaseException as e:
            raise litellm.APIConnectionError(
                message="wrapped", llm_provider="openai", model="m"
            ) from e
    except BaseException as outer:
        return outer


CASES = [
    (litellm.RateLimitError("slow down", "openai", "m", response=_resp(429)), "rate_limited"),
    (litellm.AuthenticationError("bad key", "openai", "m", response=_resp(401)), "auth"),
    (litellm.PermissionDeniedError("nope", "openai", "m", response=_resp(403)), "auth"),
    (litellm.Timeout("took too long", "m", "openai"), "timeout"),
    (TimeoutError("read timed out"), "timeout"),
    (litellm.APIConnectionError("refused", "openai", "m"), "connection"),
    (ConnectionError("reset by peer"), "connection"),
    (litellm.InternalServerError("boom", "openai", "m", response=_resp(500)), "provider_error"),
    (litellm.BadGatewayError("bad gw", "openai", "m", response=_resp(502)), "provider_error"),
    (
        litellm.ServiceUnavailableError("down", "openai", "m", response=_resp(503)),
        "provider_error",
    ),
    (
        litellm.ContextWindowExceededError(
            "maximum context length is 8192 tokens", "m", "openai", response=_resp(400)
        ),
        "context_overflow",
    ),
    (
        litellm.BadRequestError("unsupported param", "m", "openai", response=_resp(400)),
        "bad_request",
    ),
    (litellm.NotFoundError("no such model", "m", "openai", response=_resp(404)), "bad_request"),
    (AllProvidersFailed("all agent models failed or were cooling"), "all_busy"),
    (KeyError("surprise"), "unknown"),
]


@pytest.mark.parametrize(("exc", "kind"), CASES, ids=[k + ":" + type(e).__name__ for e, k in CASES])
def test_each_failure_is_named_by_its_kind(exc: BaseException, kind: str) -> None:
    assert classify_error(exc) == kind
    assert kind in ERROR_KINDS


@pytest.mark.parametrize(
    ("exc", "kind"),
    [c for c in CASES if c[1] not in {"connection", "unknown"}],
    ids=[k + ":" + type(e).__name__ for e, k in CASES if k not in {"connection", "unknown"}],
)
def test_the_cause_decides_not_the_wrapper(exc: BaseException, kind: str) -> None:
    """The 429 that started this arrives wrapped in an APIConnectionError; read
    as the wrapper it would say "connection" and send the user to the network."""
    assert classify_error(_wrapped(exc)) == kind


def test_an_exhausted_chain_that_was_rate_limited_says_rate_limited() -> None:
    """`failover/model.py` raises `AllProvidersFailed ... from` the 429 on purpose:
    a limit clears on its own schedule, busy models are a capacity question."""
    try:
        try:
            raise litellm.RateLimitError("slow", "openai", "m", response=_resp(429))
        except litellm.RateLimitError as e:
            raise AllProvidersFailed("all cooling") from e
    except AllProvidersFailed as exc:
        assert classify_error(exc) == "rate_limited"
