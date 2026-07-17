"""ITokenService — resolves the api_key to use for one LLM endpoint on a user's
behalf. There is no universal system key (each preset configures its own), so the
V1 PassthroughTokenService returns each endpoint's own key unchanged; a real impl
returns the user's personal token instead, optionally behind a per-user TTL cache.
"""

import pytest

from workspace_app.tokens import (
    CachingTokenService,
    ITokenService,
    PassthroughTokenService,
)


async def test_passthrough_returns_the_current_key_unchanged_for_any_user():
    svc = PassthroughTokenService()
    assert isinstance(svc, ITokenService)
    # v1: identity — every preset's own key is used, untouched, so external
    # behaviour is unchanged. A None key (Ollama / no auth) stays None.
    assert await svc.get_token("alice", "preset-a-key") == "preset-a-key"
    assert await svc.get_token("bob", "preset-b-key") == "preset-b-key"
    assert await svc.get_token("alice", None) is None


class _CountingSource(ITokenService):
    """A real-style source: the token depends only on the user (it ignores
    current_key), and is numbered so a cache hit is observable."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def get_token(self, user_id: str, current_key: str | None) -> str | None:
        self.calls[user_id] = self.calls.get(user_id, 0) + 1
        return f"tok-{user_id}-{self.calls[user_id]}"


async def test_caching_service_caches_per_user_ignoring_current_key():
    now = {"t": 0.0}
    src = _CountingSource()
    svc = CachingTokenService(src, ttl_seconds=100.0, _now=lambda: now["t"])
    assert await svc.get_token("alice", "k1") == "tok-alice-1"
    now["t"] = 99.0
    # cached by USER — a different endpoint (current_key) still gets the cached
    # per-user token, no re-fetch (a real user token is the same for every endpoint)
    assert await svc.get_token("alice", "k2") == "tok-alice-1"
    assert src.calls["alice"] == 1
    assert await svc.get_token("bob", "k1") == "tok-bob-1"
    assert src.calls == {"alice": 1, "bob": 1}


async def test_caching_service_refetches_after_ttl_expiry():
    now = {"t": 0.0}
    src = _CountingSource()
    svc = CachingTokenService(src, ttl_seconds=100.0, _now=lambda: now["t"])
    assert await svc.get_token("alice", "k") == "tok-alice-1"
    now["t"] = 100.0  # at the TTL boundary the entry is stale → re-fetch
    assert await svc.get_token("alice", "k") == "tok-alice-2"
    assert src.calls["alice"] == 2


async def test_caching_service_does_not_cache_a_failed_fetch():
    class _FlakySource(ITokenService):
        def __init__(self) -> None:
            self.n = 0

        async def get_token(self, user_id: str, current_key: str | None) -> str | None:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("external system down")
            return "recovered"

    svc = CachingTokenService(_FlakySource(), ttl_seconds=100.0)
    with pytest.raises(RuntimeError):
        await svc.get_token("alice", "k")
    # the failure was not cached, so the next call retries and succeeds
    assert await svc.get_token("alice", "k") == "recovered"
