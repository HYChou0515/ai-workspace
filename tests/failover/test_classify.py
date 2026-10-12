"""docs/plan-friendly-errors.md P1 — every failure a turn can end on is named by
ONE of a fixed set of kinds, so the FE can word it (D5) and the raw text can go
under technical details (D1) instead of being the whole message."""

from __future__ import annotations

import httpx
import litellm
import pytest

from workspace_app.failover.classify import ERROR_KINDS, classify_error, raw_error_text
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


def test_every_kind_has_copy_in_both_languages() -> None:
    """D5: the FE words a failure from its kind; a kind with no copy reads as
    "something unexpected went wrong" — true, and useless. The backend's set is
    the oracle, so a kind added here without copy fails HERE, not in front of a
    user."""
    import re
    from pathlib import Path

    catalog = (Path(__file__).parents[2] / "web/src/lib/i18n.tsx").read_text(encoding="utf-8")
    for kind in sorted(ERROR_KINDS):
        for part in ("what", "do"):
            m = re.search(rf'"failure\.{kind}\.{part}": \{{(.*?)\}},', catalog, re.S)
            assert m, f"no copy for failure.{kind}.{part}"
            assert '"zh-TW":' in m.group(1) and "en:" in m.group(1), f"failure.{kind}.{part}"


def _raised(build) -> BaseException:
    try:
        build()
    except BaseException as exc:  # noqa: BLE001 — the test wants whatever was raised
        return exc
    raise AssertionError("nothing raised")


def _wrapped_from(cause_mode: str) -> None:
    try:
        raise RuntimeError("exchange failed for token=hunter2")
    except RuntimeError as inner:
        if cause_mode == "from":
            raise ValueError("fixed text") from inner
        if cause_mode == "none":
            raise ValueError("fixed text") from None
        raise ValueError("fixed text")  # noqa: B904 — the implicit-context case under test


@pytest.mark.parametrize("mode", ["from", "none", "context"])
def test_the_technical_details_show_the_links_a_traceback_shows(mode):
    """`raw_error_text` is what a reader sees as technical details, so it shows
    exactly the links Python's own traceback would — the oracle here — and no
    more. `raise … from None` is how code says a cause must not be shown: the
    workflow executor uses it to keep a deployment's env-source message (which
    may hold the token it was exchanging) out of a run record every
    participant reads. Walking past it put `token=hunter2` in that record."""
    import traceback

    exc = _raised(lambda: _wrapped_from(mode))
    shown = [
        line.split(":", 1)[0]
        for line in traceback.format_exception(exc)
        if line and not line.startswith((" ", "Traceback", "\n", "During", "The above"))
    ]

    assert [link.split(":", 1)[0] for link in raw_error_text(exc).split("\n← ")][::-1] == shown
