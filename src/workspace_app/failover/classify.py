"""Name the failure a model call ended on (docs/plan-friendly-errors.md D6).

A turn used to end on ``f"{type(exc).__name__}: {exc}"`` — the provider's own
string, which tells a reader neither what happened nor what to do. The kind is
what the FE words (D5) while the raw text goes under technical details (D1).

The kinds are a closed set the FE has copy for; anything unrecognised is
``unknown``, never a new string the FE would have to fall back on.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

import httpx

from ..context_budget import is_context_overflow
from .core import AllProvidersFailed
from .rate_limit import _chain, is_rate_limited

ERROR_KINDS: Final = frozenset(
    {
        "rate_limited",
        "all_busy",
        "auth",
        "timeout",
        "connection",
        "provider_error",
        "context_overflow",
        "bad_request",
        "unknown",
    }
)

# litellm's `Timeout` subclasses `APIConnectionError`, and `APIConnectionError`
# carries `status_code = 500` — so names are read BEFORE the status code, or a
# dropped connection would read as the provider failing.
_TIMEOUT_NAMES = frozenset({"Timeout", "APITimeoutError", "ReadTimeout", "ConnectTimeout"})
_CONNECTION_NAMES = frozenset({"APIConnectionError", "ConnectError", "RemoteProtocolError"})


def _link_kind(exc: BaseException) -> str:
    """The kind ONE link names, ignoring what it wraps."""
    name = type(exc).__name__
    if name == "ContextWindowExceededError" or is_context_overflow(str(exc)):
        return "context_overflow"
    if isinstance(exc, TimeoutError) or name in _TIMEOUT_NAMES:
        return "timeout"
    if isinstance(exc, ConnectionError) or name in _CONNECTION_NAMES:
        return "connection"
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        if code in (401, 403):
            return "auth"
        if code == 408:
            return "timeout"
        if code >= 500:
            return "provider_error"
        if 400 <= code < 500:
            return "bad_request"
    return "unknown"


def _is_transport(link: BaseException) -> bool:
    """A failure of the connection itself, raised by the network stack — not a
    wrapper's reading of one."""
    return isinstance(link, (httpx.TransportError, TimeoutError, ConnectionError))


def _is_timeout(link: BaseException) -> bool:
    return isinstance(link, (httpx.TimeoutException, TimeoutError))


def classify_error(exc: BaseException) -> str:
    """One of :data:`ERROR_KINDS` for ``exc``.

    The CAUSE decides, not the wrapper: litellm and the agents SDK re-raise the
    real failure inside an ``APIConnectionError``, so the outermost link alone
    would send a reader to the network for a rate limit. ``connection`` is
    therefore the weakest reading — taken only when no link says anything more
    specific."""
    if is_rate_limited(exc):
        return "rate_limited"
    links = list(_chain(exc))
    if any(isinstance(link, AllProvidersFailed) for link in links):
        return "all_busy"
    # The network's own word beats a status code the client invented (plan P8,
    # D12): litellm puts a 500/503 of its own over a refused connection
    # (`InternalServerError`) and a stream that broke or went quiet
    # (`MidStreamFallbackError`), and read outermost-first that said "the
    # provider failed" for a model nobody reached. A response the server DID
    # send ends in `httpx.HTTPStatusError`, which is not a transport error.
    transport = [link for link in links if _is_transport(link)]
    if transport:
        return "timeout" if any(_is_timeout(link) for link in transport) else "connection"
    kinds = [_link_kind(link) for link in links]
    for kind in kinds:
        if kind not in ("connection", "unknown"):
            return kind
    return "connection" if "connection" in kinds else "unknown"


class ModelCallFailed(Exception):
    """A model call was given up on after its retries (``agent.call_retry_model``).
    Raised FROM the last failure, so the kind is still read off the cause; it
    carries only the numbers the kind's copy needs."""

    def __init__(self, *, attempts: int, waited_s: float) -> None:
        super().__init__(f"gave up on the model call after {attempts} attempt(s)")
        self.attempts = attempts
        self.waited_s = waited_s


def error_params(exc: BaseException) -> dict[str, int] | None:
    """The numbers D5's copy says — how long a rate limit was waited out, or
    how many times a call was tried — when a give-up recorded them."""
    for link in _chain(exc):
        if isinstance(link, ModelCallFailed):
            if is_rate_limited(exc):
                return {"waited_s": round(link.waited_s)}
            return {"attempts": link.attempts}
    return None


def raw_error_text(exc: BaseException) -> str:
    """The technical details (D1): every link's ``Type: message``, outermost
    first. The outer link alone is often the least useful one — an exhausted
    chain reads ``AllProvidersFailed: …`` while the 429 that WAS the failure
    sits one link down."""
    return "\n← ".join(f"{type(link).__name__}: {link}" for link in _shown_chain(exc))


def _shown_chain(exc: BaseException) -> Iterator[BaseException]:
    """The links a traceback would show — and a reader may see. Unlike
    `_chain` (which looks everywhere for a 429), it stops where the code said
    ``raise … from None``: that is how a boundary keeps a message out of what
    everyone reads, e.g. a deployment's env source whose error may hold the
    token it was exchanging (`workflow_exec`)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or (None if cur.__suppress_context__ else cur.__context__)
