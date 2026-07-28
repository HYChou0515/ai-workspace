"""ITokenService implementations: the behaviour-preserving V1 passthrough and a
per-user TTL cache in front of a real (user-keyed) source."""

from __future__ import annotations

import time
from collections.abc import Callable

from .protocol import CallLane, ITokenService, LlmCredential


class PassthroughTokenService(ITokenService):
    """V1: return each endpoint's own key unchanged (``current_key``).

    There is no universal system key to hand out — every preset configures its
    own — so V1 is the identity: whatever key a turn's endpoint would otherwise
    use, it keeps using. External behaviour is byte-for-byte unchanged. The later
    real impl replaces this with a user-keyed source; the seam and its callers do
    not change."""

    async def get_credential(
        self, user_id: str, current_key: str | None, lane: CallLane
    ) -> LlmCredential:
        return LlmCredential(current_key)


class CachingTokenService(ITokenService):
    """A per-user TTL cache in front of a real (user-keyed) source.

    For a real source the credential depends on the user and the lane, so the cache
    is keyed on ``(user_id, lane)`` and ``current_key`` is passed to the inner source
    but is NOT part of the cache key. (Do not wrap a passthrough/identity source —
    its result depends on ``current_key``, which this cache deliberately ignores.)

    The **lane belongs in the key**: a background job served the interactive
    credential is exactly the mix-up the lane exists to prevent — the gateway would
    count the batch against the quota a person is waiting on.

    Simple ``{(user_id, lane): (credential, expires_at)}`` — a stale entry is
    re-fetched; concurrent misses for the same user may each fetch (no
    single-flight, by design). Only successes are cached (a raised fetch is not
    stored). ``_now`` is injectable so TTL expiry is testable deterministically."""

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
        self._cache: dict[tuple[str, CallLane], tuple[LlmCredential, float]] = {}

    async def get_credential(
        self, user_id: str, current_key: str | None, lane: CallLane
    ) -> LlmCredential:
        now = self._now()
        cached = self._cache.get((user_id, lane))
        if cached is not None and cached[1] > now:
            return cached[0]
        credential = await self._inner.get_credential(user_id, current_key, lane)
        self._cache[user_id, lane] = (credential, now + self._ttl)
        return credential
