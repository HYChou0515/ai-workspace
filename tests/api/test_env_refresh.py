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


def test_layer_of_names_the_service_account_below_the_person():
    from workspace_app.api.env_layers import layer_of

    svc = {"K": "s"}
    assert layer_of("K", shared={}, private={}, personal={}, service=svc, policy={}) == "service"
    mine = {"K": "v"}
    assert layer_of("K", shared={}, private={}, personal=mine, service=svc, policy={}) == "personal"
