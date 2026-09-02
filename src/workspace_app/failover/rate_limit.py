"""Rate limiting (429) — the transient whose cure is waiting, not switching.

``is_transient`` lumps 429 in with gateway blips, and for the index path that is
fine. For everything else it is wrong in two ways: a rate-limited endpoint is
not *broken*, so parking it on cooldown and switching away spends a healthy
provider; and the recovery is a *duration*, which a fixed retry gap cannot
guess. The provider usually states that duration in the response — this module
reads it back out.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from email.utils import parsedate_to_datetime


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """``exc`` and everything under it. litellm/the agents-SDK re-raise the real
    cause wrapped in an ``APIConnectionError``, so the 429 — and the headers
    that say how long to wait — sit one or more links down. Same cycle-safe
    walk as ``turns._is_all_busy``."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__


# A 429 that states no Retry-After still must not be re-sent at blip speed —
# that cadence is what earned the throttle. Same doubling shape as the chain
# re-sweep default (`failover.round_backoff_s`), capped so one unlucky turn
# cannot park for minutes.
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 16.0


def backoff_s(attempt: int) -> float:
    """1s, 2s, 4s … capped — the wait for a 429 that didn't say how long."""
    return min(_BACKOFF_BASE_S * 2 ** max(0, attempt - 1), _BACKOFF_CAP_S)


def rate_limit_wait_s(exc: BaseException, *, attempt: int) -> float:
    """How long to hold before re-sending after ``exc`` rate-limited us: the
    window the provider stated, else a doubling backoff on ``attempt`` — and
    never LESS than that backoff either way. ``Retry-After`` is a floor, not an
    exact figure, so oversleeping a stated ``0.5`` to 1s is harmless; honouring
    a stated ``0`` (or ``0.001``) literally is not — a provider that repeats it
    while still refusing would be re-sent to at wire speed, the very cadence
    that earned the throttle, and a time budget never spends against zero-length
    waits."""
    stated = retry_after_s(exc)
    floor = backoff_s(attempt)
    return max(stated, floor) if stated is not None else floor


def park_for(cooldown_s: float, cause: BaseException) -> float:
    """How long to bench a provider that just failed.

    ``cooldown_s`` is the bench time for an endpoint that is *unwell*. A
    rate-limited one is healthy — it serves again the moment its window rolls —
    so when the 429 states a wait, that is the honest bench time. The registry
    is keyed by ``(model, endpoint)`` and shared across roles, so parking for
    exactly the stated window also tells every other caller to hold off,
    instead of each rediscovering the limit one 429 at a time.
    """
    if is_rate_limited(cause):
        stated = retry_after_s(cause)
        if stated is not None:
            return stated
    return cooldown_s


def retry_pause(cause: BaseException) -> float:
    """How long to hold before retrying the SAME endpoint. Zero for an ordinary
    pre-first failure (the retry is meant to be quick), the stated window when
    the endpoint rate-limited us."""
    return retry_after_s(cause) or 0.0 if is_rate_limited(cause) else 0.0


def is_rate_limited(exc: BaseException) -> bool:
    """Whether ``exc`` is the provider saying "too fast", as opposed to any
    other transient. The distinction decides the recovery: a rate limit is
    cured by waiting at the SAME endpoint, an unwell one by switching away."""
    return any(getattr(e, "status_code", None) == 429 for e in _chain(exc))


def retry_after_s(exc: BaseException, *, now: Callable[[], float] = time.time) -> float | None:
    """How long the provider asked us to wait, or ``None`` when it didn't say."""
    for link in _chain(exc):
        headers = getattr(getattr(link, "response", None), "headers", None)
        raw = headers.get("retry-after") if headers is not None else None
        if raw is not None:
            return _parse_retry_after(raw, now)
    return None


def _parse_retry_after(raw: str, now: Callable[[], float]) -> float | None:
    """RFC 7231 §7.1.3 allows either delay-seconds or an absolute HTTP-date.

    Anything else reads as "the provider didn't say" rather than raising: this
    runs inside an ``except`` block, so a throw here would turn a recoverable
    rate limit into a hard failure.
    """
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(raw).timestamp() - now())
    except (TypeError, ValueError):
        return None
