"""ITokenService implementations: the behaviour-preserving V1 source and a
per-user TTL cache in front of any source."""

from __future__ import annotations

import time
from collections.abc import Callable

from .protocol import ITokenService


class SystemTokenService(ITokenService):
    """V1 source: return the single system token, ignoring the user id — so
    external behaviour is identical to a baked ``api_key``. The later real impl
    replaces this inner source; the seam and its callers do not change."""

    def __init__(self, system_token: str) -> None:
        self._system_token = system_token

    async def get_token(self, user_id: str) -> str:
        return self._system_token


class CachingTokenService(ITokenService):
    """A per-user TTL cache in front of any inner :class:`ITokenService`.

    Wraps the *source* so the (future) external fetch runs at most once per user
    per ``ttl_seconds``. Simple ``{user_id: (token, expires_at)}`` — a stale
    entry is re-fetched; concurrent misses for the same user may each fetch (no
    single-flight, by design). Only successes are cached (a raised fetch is not
    stored). ``_now`` is injectable so TTL expiry is testable deterministically.
    """

    def __init__(
        self,
        inner: ITokenService,
        ttl_seconds: float = 300.0,
        *,
        _now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._ttl = ttl_seconds
        self._now = _now
        self._cache: dict[str, tuple[str, float]] = {}

    async def get_token(self, user_id: str) -> str:
        now = self._now()
        cached = self._cache.get(user_id)
        if cached is not None and cached[1] > now:
            return cached[0]
        token = await self._inner.get_token(user_id)
        self._cache[user_id] = (token, now + self._ttl)
        return token
