"""Run-scoped credentials (#100, manual §15) — mint maps a token to its captured
user + run, it expires, and it is revoked when the run ends."""

from workspace_app.workflow.credential import CredentialBroker


def _clock(start: int = 1000):
    t = [start]

    def now() -> int:
        return t[0]

    return t, now


def test_mint_then_resolve_maps_to_user_and_run():
    t, now = _clock()
    broker = CredentialBroker(now=now)
    token = broker.mint(run_id="r1", user="alice", item_id="i1", ttl_ms=1000)
    claims = broker.resolve(token)
    assert claims is not None
    assert (claims.user, claims.run_id, claims.item_id) == ("alice", "r1", "i1")


def test_unknown_token_resolves_to_none():
    assert CredentialBroker().resolve("nope") is None


def test_token_expires_after_ttl():
    t, now = _clock()
    broker = CredentialBroker(now=now)
    token = broker.mint(run_id="r1", user="u", item_id="i1", ttl_ms=500)
    t[0] = 1499
    assert broker.resolve(token) is not None  # still inside the window
    t[0] = 1500
    assert broker.resolve(token) is None  # expired → dropped


def test_revoke_drops_all_run_tokens():
    broker = CredentialBroker()
    a = broker.mint(run_id="r1", user="u", item_id="i1", ttl_ms=10_000)
    b = broker.mint(run_id="r2", user="u", item_id="i2", ttl_ms=10_000)
    broker.revoke("r1")
    assert broker.resolve(a) is None
    assert broker.resolve(b) is not None  # a different run's token is untouched
