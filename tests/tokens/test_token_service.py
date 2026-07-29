"""ITokenService — resolves the api_key to use for one LLM endpoint on a user's
behalf. There is no universal system key (each preset configures its own), so the
V1 PassthroughTokenService returns each endpoint's own key unchanged; a real impl
returns the user's personal token instead, optionally behind a per-user TTL cache.
"""

import pytest

from workspace_app.tokens import (
    CachingTokenService,
    CallLane,
    ITokenService,
    LlmCredential,
    PassthroughTokenService,
)


async def test_passthrough_returns_the_current_key_unchanged_for_any_user():
    svc = PassthroughTokenService()
    assert isinstance(svc, ITokenService)
    # v1: identity — every preset's own key is used, untouched, so external
    # behaviour is unchanged. A None key (Ollama / no auth) stays None. No
    # headers either: the default deploy sends exactly what it sent before.
    assert await svc.get_credential("alice", "preset-a-key", "interactive") == LlmCredential(
        "preset-a-key"
    )
    assert await svc.get_credential("bob", "preset-b-key", "background") == LlmCredential(
        "preset-b-key"
    )
    assert await svc.get_credential("alice", None, "interactive") == LlmCredential(None)


async def test_a_credential_carries_extra_headers_for_the_call():
    # The point of the widened seam: a real impl authenticates with something
    # that is NOT an api_key (a session cookie) and/or tags the call's lane for
    # the gateway's rate limiter. Both are headers; we are only the courier, so
    # the seam never names them.
    cred = LlmCredential("k", {"Cookie": "session=abc", "X-Lane": "background"})
    assert cred.api_key == "k"
    assert cred.headers == {"Cookie": "session=abc", "X-Lane": "background"}


class _CountingSource(ITokenService):
    """A real-style source: the credential depends only on the user and the lane
    (it ignores current_key), and is numbered so a cache hit is observable."""

    def __init__(self) -> None:
        self.calls: dict[tuple[str, str], int] = {}

    async def get_credential(
        self, user_id: str, current_key: str | None, lane: CallLane
    ) -> LlmCredential:
        n = self.calls[user_id, lane] = self.calls.get((user_id, lane), 0) + 1
        return LlmCredential(f"tok-{user_id}-{n}", {"X-Lane": lane})


async def test_caching_service_caches_per_user_ignoring_current_key():
    now = {"t": 0.0}
    src = _CountingSource()
    svc = CachingTokenService(src, ttl_seconds=100.0, _now=lambda: now["t"])
    assert (await svc.get_credential("alice", "k1", "interactive")).api_key == "tok-alice-1"
    now["t"] = 99.0
    # cached by USER — a different endpoint (current_key) still gets the cached
    # per-user credential, no re-fetch (a real user token is the same for every
    # endpoint)
    assert (await svc.get_credential("alice", "k2", "interactive")).api_key == "tok-alice-1"
    assert src.calls["alice", "interactive"] == 1
    assert (await svc.get_credential("bob", "k1", "interactive")).api_key == "tok-bob-1"
    assert src.calls == {("alice", "interactive"): 1, ("bob", "interactive"): 1}


async def test_caching_service_does_not_serve_one_lane_the_other_lane_credential():
    # The lane is the whole point: a background job must not be handed the
    # credential a person's interactive turn just cached, or the gateway counts
    # the batch against the interactive quota and the distinction buys nothing.
    now = {"t": 0.0}
    src = _CountingSource()
    svc = CachingTokenService(src, ttl_seconds=100.0, _now=lambda: now["t"])
    interactive = await svc.get_credential("alice", "k", "interactive")
    background = await svc.get_credential("alice", "k", "background")
    assert interactive.headers == {"X-Lane": "interactive"}
    assert background.headers == {"X-Lane": "background"}
    # each lane is cached on its own
    assert (await svc.get_credential("alice", "k", "background")) == background
    assert src.calls == {("alice", "interactive"): 1, ("alice", "background"): 1}


async def test_caching_service_refetches_after_ttl_expiry():
    now = {"t": 0.0}
    src = _CountingSource()
    svc = CachingTokenService(src, ttl_seconds=100.0, _now=lambda: now["t"])
    assert (await svc.get_credential("alice", "k", "interactive")).api_key == "tok-alice-1"
    now["t"] = 100.0  # at the TTL boundary the entry is stale → re-fetch
    assert (await svc.get_credential("alice", "k", "interactive")).api_key == "tok-alice-2"
    assert src.calls["alice", "interactive"] == 2


async def test_caching_service_does_not_cache_a_failed_fetch():
    class _FlakySource(ITokenService):
        def __init__(self) -> None:
            self.n = 0

        async def get_credential(
            self, user_id: str, current_key: str | None, lane: CallLane
        ) -> LlmCredential:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("external system down")
            return LlmCredential("recovered")

    svc = CachingTokenService(_FlakySource(), ttl_seconds=100.0)
    with pytest.raises(RuntimeError):
        await svc.get_credential("alice", "k", "interactive")
    # the failure was not cached, so the next call retries and succeeds
    assert (await svc.get_credential("alice", "k", "interactive")).api_key == "recovered"
