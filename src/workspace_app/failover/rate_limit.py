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
