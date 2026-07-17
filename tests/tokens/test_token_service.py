"""ITokenService — maps a user id to that user's API token for the external
(LLM) system. V1 is behaviour-preserving: it hands back the system token for
everyone, so the seam exists to be swapped for a real per-user impl later.
"""

import pytest

from workspace_app.tokens import (
    CachingTokenService,
    ITokenService,
    SystemTokenService,
)


async def test_system_token_service_returns_the_system_token_for_any_user():
    svc = SystemTokenService("sys-key")
    assert isinstance(svc, ITokenService)
    # v1: the user id is ignored — everyone gets the one system token, so
    # external behaviour is unchanged from "one baked api_key".
    assert await svc.get_token("alice") == "sys-key"
    assert await svc.get_token("bob") == "sys-key"


class _CountingSource(ITokenService):
    """A source that returns a fresh, per-user-numbered token each call, so a
    cache hit (no re-fetch) is observable in the returned value + the call count."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def get_token(self, user_id: str) -> str:
        self.calls[user_id] = self.calls.get(user_id, 0) + 1
        return f"tok-{user_id}-{self.calls[user_id]}"


async def test_caching_service_caches_per_user_within_ttl():
    now = {"t": 0.0}
    src = _CountingSource()
    svc = CachingTokenService(src, ttl_seconds=100.0, _now=lambda: now["t"])
    assert await svc.get_token("alice") == "tok-alice-1"
    now["t"] = 99.0  # still within the 100s TTL
    assert await svc.get_token("alice") == "tok-alice-1"  # cache hit, no re-fetch
    assert src.calls["alice"] == 1
    # a different user is a separate cache entry
    assert await svc.get_token("bob") == "tok-bob-1"
    assert src.calls == {"alice": 1, "bob": 1}


async def test_caching_service_refetches_after_ttl_expiry():
    now = {"t": 0.0}
    src = _CountingSource()
    svc = CachingTokenService(src, ttl_seconds=100.0, _now=lambda: now["t"])
    assert await svc.get_token("alice") == "tok-alice-1"
    now["t"] = 100.0  # at the TTL boundary the entry is stale → re-fetch
    assert await svc.get_token("alice") == "tok-alice-2"
    assert src.calls["alice"] == 2


async def test_caching_service_does_not_cache_a_failed_fetch():
    class _FlakySource(ITokenService):
        def __init__(self) -> None:
            self.n = 0

        async def get_token(self, user_id: str) -> str:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("external system down")
            return "recovered"

    svc = CachingTokenService(_FlakySource(), ttl_seconds=100.0)
    with pytest.raises(RuntimeError):
        await svc.get_token("alice")
    # the failure was not cached, so the next call retries and succeeds
    assert await svc.get_token("alice") == "recovered"
