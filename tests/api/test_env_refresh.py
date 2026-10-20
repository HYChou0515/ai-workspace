"""The platform refreshes an expiring token before a tool runs
(`docs/plan-env-token-refresh.md`).

The deploy's sign-in (`IEnvProvider`) gains an optional refresh step (D1). The
platform is an interface: it knows a refresh failed only by the reporting rule
it states (D5) — `refresh()` returning is success, `RefreshRejected(names)` is
a refresh token that is dead, anything else is a passing failure.
"""

from __future__ import annotations

import pytest

from workspace_app.api.env_provider import (
    IEnvProvider,
    InputField,
    RefreshRejected,
    supports_refresh,
)


class _Plain(IEnvProvider):
    """A deploy written before refresh existed: nothing it does may change."""

    @property
    def id(self) -> str:
        return "plain"

    @property
    def label(self) -> str:
        return "Plain"

    @property
    def produces(self) -> frozenset[str]:
        return frozenset({"TOKEN"})

    @property
    def inputs(self) -> tuple[InputField, ...]:
        return ()

    async def resolve(self, values: dict[str, str]) -> dict[str, str]:
        return {"TOKEN": "t"}


class _Refreshing(_Plain):
    def needs_refresh(self, values: dict[str, str]) -> bool:
        return values.get("TOKEN") == "old"

    async def refresh(self, values: dict[str, str]) -> dict[str, str]:
        return {"TOKEN": "new"}


# ─── P1: the provider's side of the contract ─────────────────────────────────


def test_a_provider_written_before_refresh_never_asks_for_one():
    plain = _Plain()
    assert plain.needs_refresh({"TOKEN": "old"}) is False
    assert supports_refresh(plain) is False


async def test_a_provider_written_before_refresh_cannot_be_asked_to_refresh():
    with pytest.raises(NotImplementedError):
        await _Plain().refresh({"TOKEN": "old"})


def test_a_provider_that_overrides_refresh_supports_it():
    assert supports_refresh(_Refreshing()) is True


def test_a_rejection_names_what_is_dead():
    """D5: only the deploy knows which variable is the refresh token, so it
    names what to clear."""
    err = RefreshRejected({"TOKEN", "REFRESH"})
    assert err.names == frozenset({"TOKEN", "REFRESH"})
    # Never the values: what reaches a log is the names only.
    assert "TOKEN" in str(err) and "REFRESH" in str(err)


# ─── P2: which layer a value comes from ──────────────────────────────────────


def _cases() -> list[dict]:
    import json
    from pathlib import Path

    path = Path(__file__).parents[1] / "fixtures" / "env_layers_cases.json"
    return json.loads(path.read_text())["cases"]


@pytest.mark.parametrize("case", _cases())
def test_layer_of_names_the_layer_the_shared_table_expects(case):
    """The write-back (D3) must land in the layer the tool's value came from.
    `layer_of` is that answer; `env_layers_cases.json` is the table the FE's
    `layerInUse` and `resolve_env` are already held to."""
    from workspace_app.api.env_layers import layer_of

    got = layer_of(
        "K",
        shared=case["shared"],
        private=case["private"],
        personal=case["personal"],
        policy=case["policy"],
    )
    assert got == case["expected"], case


# ─── P3: one refresh at a time, across pods ──────────────────────────────────


def _claims(clock: list[int]):
    from workspace_app.api.refresh_claims import RefreshClaims, register_refresh_claims
    from workspace_app.resources import make_spec

    spec = make_spec()
    register_refresh_claims(spec)
    return RefreshClaims(spec, now=lambda: clock[0])


def test_one_taker_holds_a_key_and_a_second_is_refused():
    """A rotating refresh token is spent once: two runs that both see it near
    expiry must not both send it."""
    claims = _claims([1_000])
    first = claims.try_take("personal:alice:erp", ttl_ms=30_000)
    assert first is not None
    assert claims.try_take("personal:alice:erp", ttl_ms=30_000) is None


def test_keys_are_independent():
    claims = _claims([1_000])
    assert claims.try_take("personal:alice:erp", ttl_ms=30_000) is not None
    assert claims.try_take("personal:bob:erp", ttl_ms=30_000) is not None


def test_a_holder_that_died_does_not_block_for_ever():
    clock = [1_000]
    claims = _claims(clock)
    assert claims.try_take("k", ttl_ms=30_000) is not None
    clock[0] = 1_000 + 30_001
    assert claims.try_take("k", ttl_ms=30_000) is not None


def test_release_frees_the_key_for_the_next_taker():
    claims = _claims([1_000])
    holder = claims.try_take("k", ttl_ms=30_000)
    assert holder is not None
    claims.release("k", holder)
    assert claims.try_take("k", ttl_ms=30_000) is not None


def test_releasing_a_key_nobody_took_is_harmless():
    _claims([1_000]).release("never-taken", "anyone")


def test_only_the_holder_can_release():
    """A late release from a taker whose claim expired and passed to another
    must not free the new holder's key."""
    clock = [1_000]
    claims = _claims(clock)
    old = claims.try_take("k", ttl_ms=30_000)
    clock[0] = 1_000 + 30_001
    new = claims.try_take("k", ttl_ms=30_000)
    assert old and new and old != new
    claims.release("k", old)
    assert claims.try_take("k", ttl_ms=30_000) is None


def test_held_says_whether_a_waiter_should_keep_waiting():
    """A holder that let go (or died) frees the waiter at once."""
    clock = [1_000]
    claims = _claims(clock)
    assert not claims.held("never-taken")
    holder = claims.try_take("k", ttl_ms=30_000)
    assert holder is not None
    assert claims.held("k")
    claims.release("k", holder)
    assert not claims.held("k")
    assert claims.try_take("k", ttl_ms=30_000) is not None
    clock[0] = 1_000 + 30_001
    assert not claims.held("k")


def test_layer_of_names_the_service_account_below_the_person():
    from workspace_app.api.env_layers import layer_of

    svc = {"K": "s"}
    assert layer_of("K", shared={}, private={}, personal={}, service=svc, policy={}) == "service"
    mine = {"K": "v"}
    assert layer_of("K", shared={}, private={}, personal=mine, service=svc, policy={}) == "personal"
