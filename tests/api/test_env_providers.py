"""#750 — "log in, get the variables", the deploy's own code doing the exchange.

A tool declares variable NAMES; a provider declares the names it produces; the
panel joins them on the name. The tool never names a provider, so a third-party
author cannot choose which credential our interface asks a person for.

The credential reaches the deploy's implementation and stops there: nothing here
stores it, returns it, or writes the result. The endpoint hands the variables
back to the panel, which puts them in the form — the person still presses Save.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI

from workspace_app.api.env_provider import IEnvProvider, InputField

from .conftest import Harness, register_rca_item


class _SapLogin(IEnvProvider):
    """A stand-in for a deploy's own login: swaps a password for a token."""

    def __init__(self) -> None:
        self.seen: list[dict[str, str]] = []

    @property
    def id(self) -> str:
        return "sap-login"

    @property
    def label(self) -> str:
        return "SAP production login"

    @property
    def produces(self) -> frozenset[str]:
        return frozenset({"SAP_TOKEN", "SAP_HOST"})

    @property
    def inputs(self) -> tuple[InputField, ...]:
        return (
            InputField("user", "Account"),
            InputField("password", "Password", secret=True),
        )

    async def resolve(self, values: dict[str, str]) -> dict[str, str]:
        self.seen.append(dict(values))
        return {"SAP_TOKEN": f"tok-for-{values['user']}", "SAP_HOST": "sap.corp"}


class _Broken(_SapLogin):
    @property
    def id(self) -> str:
        return "broken"

    async def resolve(self, values: dict[str, str]) -> dict[str, str]:
        raise RuntimeError("the gateway said no")


def _with_providers(harness: Harness, *providers: IEnvProvider) -> None:
    """Swap in this deploy's implementations. They live on app state rather
    than being closed over at build time, so a test can vary them without
    rebuilding the app — the same seam a deploy reloading config would use."""
    app = harness.spa_client.app
    assert isinstance(app, FastAPI)
    app.state.env_providers = list(providers)


def test_a_provider_is_offered_for_the_variables_it_can_fill(harness: Harness):
    """The join is the variable NAME, and nothing else.

    The provider says what it produces; the panel already knows what the item's
    tools asked for. Neither side wrote an identifier belonging to the other."""
    _with_providers(harness, _SapLogin())
    iid = register_rca_item(harness.spec)

    body = harness.client.get(f"/a/rca/items/{iid}/env-providers").json()

    (offer,) = body["providers"]
    assert offer["id"] == "sap-login"
    assert offer["label"] == "SAP production login"
    assert sorted(offer["produces"]) == ["SAP_HOST", "SAP_TOKEN"]
    # The dialog is described by the provider, because only it knows what its
    # own system needs collected.
    assert offer["inputs"] == [
        {"name": "user", "label": "Account", "secret": False},
        {"name": "password", "label": "Password", "secret": True},
    ]


def test_resolving_returns_the_variables_and_stores_nothing(harness: Harness):
    """The exchange happens; the item is not touched.

    The result goes to the panel's form, not to the record — the person still
    presses Save. Writing here would give one dialog two save semantics with
    nothing on screen to tell them apart."""
    provider = _SapLogin()
    _with_providers(harness, provider)
    iid = register_rca_item(harness.spec)

    resp = harness.client.post(
        f"/a/rca/items/{iid}/env-providers/sap-login",
        json={"values": {"user": "alice", "password": "hunter2"}},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["env"] == {"SAP_TOKEN": "tok-for-alice", "SAP_HOST": "sap.corp"}
    assert provider.seen == [{"user": "alice", "password": "hunter2"}]
    # Nothing was written: the item's own variables are untouched.
    from workspace_app.apps.resolve import find_work_item

    found = find_work_item(harness.spec, iid)
    assert found is not None
    assert found[1].env_vars == {}


def test_the_credential_is_not_in_the_answer(harness: Harness):
    """What comes back is the product, never the password that bought it."""
    _with_providers(harness, _SapLogin())
    iid = register_rca_item(harness.spec)

    resp = harness.client.post(
        f"/a/rca/items/{iid}/env-providers/sap-login",
        json={"values": {"user": "alice", "password": "hunter2"}},
    )

    assert "hunter2" not in resp.text


def test_a_failed_exchange_is_reported_without_a_gateway_status(harness: Harness):
    """A refusal must be one the chat client will actually show.

    The FE reads 502/503/504 as "an idle proxy cut the connection while the work
    continues" and waits for a result that is never coming (#714). A wrong
    password has to arrive as something visible instead."""
    _with_providers(harness, _Broken())
    iid = register_rca_item(harness.spec)

    resp = harness.client.post(
        f"/a/rca/items/{iid}/env-providers/broken",
        json={"values": {"user": "a", "password": "b"}},
    )

    assert resp.status_code not in (502, 503, 504)
    assert resp.status_code >= 400


def test_a_failure_does_not_write_the_credential_into_the_log(harness: Harness, caplog):
    """A refused exchange logs which provider and why. Not what was typed.

    The failure path is the one that reaches for context, and a log line is the
    easiest place for a password to end up somewhere it will be kept for
    ninety days and read by people who were never given it. Asserted rather
    than intended: the route logs the exception's text, and an implementation
    that put the credential in its message would carry it here — which is why
    the seam's docstring says not to, and why this checks the platform's own
    half regardless."""
    _with_providers(harness, _Broken())
    iid = register_rca_item(harness.spec)

    with caplog.at_level(logging.DEBUG):
        harness.client.post(
            f"/a/rca/items/{iid}/env-providers/broken",
            json={"values": {"user": "alice", "password": "hunter2"}},
        )

    assert "hunter2" not in caplog.text
    # And it did say something — a silent failure is its own defect, so this
    # cannot pass by logging nothing at all.
    assert "broken" in caplog.text


class _Rude(_SapLogin):
    """A second-party implementation that raises while merely describing itself."""

    @property
    def id(self) -> str:
        return "rude"

    @property
    def produces(self) -> frozenset[str]:
        raise RuntimeError("this deploy's impl has a bug in a property")


def test_one_broken_implementation_does_not_remove_the_others(harness: Harness, caplog):
    """A deploy's own bug costs its own button, not everyone else's.

    Describing a provider runs second-party code — four properties of it — and
    an exception in any of them would otherwise 500 the list. The panel reads
    that as an empty list and draws no buttons at all: a deploy with three
    working logins loses all three because a fourth has a typo, with nothing
    on screen and nothing in the response to say why. Same posture as
    `discover_packages`, which degrades rather than taking a startup down."""
    _with_providers(harness, _Rude(), _SapLogin())
    iid = register_rca_item(harness.spec)

    body = harness.client.get(f"/a/rca/items/{iid}/env-providers")

    assert body.status_code == 200, body.text
    assert [p["id"] for p in body.json()["providers"]] == ["sap-login"]
    # And the operator is told which one, or the loss is undiagnosable.
    assert "rude" in caplog.text


def test_two_implementations_claiming_one_id_is_refused_at_startup():
    """A duplicate id is a config error, not a coin toss.

    The whole design rests on the id being the DEPLOY's own unambiguous name —
    it is the one identifier a third party can never write. Two implementations
    claiming it would make the dispatch pick whichever came first in the config
    file, silently, and a person would type a password into a form belonging to
    the other one."""
    import pytest

    from workspace_app.factories import get_env_providers

    with pytest.raises(ValueError, match="sap-login"):
        get_env_providers(
            [
                "tests.api.test_env_providers._SapLogin",
                "tests.api.test_env_providers._SapLoginTwin",
            ]
        )


class _SapLoginTwin(_SapLogin):
    """A second implementation that claims the same id — the collision."""


def test_an_unknown_provider_is_a_404(harness: Harness):
    _with_providers(harness, _SapLogin())
    iid = register_rca_item(harness.spec)
    resp = harness.client.post(f"/a/rca/items/{iid}/env-providers/nope", json={"values": {}})
    assert resp.status_code == 404


def test_a_participant_cannot_mint_a_token_they_could_not_have_stored():
    """The exchange is gated on `write_meta`, the verb for storing a variable.

    A Participant can READ this item's variables but not write them (#673). If
    the exchange were gated any looser, that same person could press the button,
    and the token would come back in the response body — they would have minted
    a credential's product for an item they cannot configure, and read it,
    without ever touching the field the permission guards.

    A real second identity, not the owner with a flag flipped: the rule being
    tested is about two people, and one identity cannot exercise it."""
    from specstar import SpecStar
    from starlette.testclient import TestClient

    from workspace_app.api.app import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.sandbox.mock import MockSandbox

    from .test_item_env_vars import _participant_item

    holder = {"id": "alice"}
    spec: SpecStar = _make_spec_as(holder)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_scripted(),
        get_user_id=lambda: holder["id"],
    )
    app.state.env_providers = [_SapLogin()]
    client = TestClient(app)
    rid = _participant_item(spec, owner="bob", guest="alice", env={})

    refused = client.post(
        f"/api/a/rca/items/{rid}/env-providers/sap-login",
        json={"values": {"user": "alice", "password": "x"}},
    )
    assert refused.status_code == 403, refused.text

    holder["id"] = "bob"  # the owner, who could store it by hand, still can
    allowed = client.post(
        f"/api/a/rca/items/{rid}/env-providers/sap-login",
        json={"values": {"user": "bob", "password": "x"}},
    )
    assert allowed.status_code == 200, allowed.text


def _make_spec_as(holder: dict[str, str]):
    from .test_item_env_vars import make_spec

    return make_spec(default_user=lambda: holder["id"])


def _scripted():
    from workspace_app.api.runner import ScriptedAgentRunner

    return ScriptedAgentRunner([])


@pytest.mark.parametrize("configured", [True, False])
def test_a_deploy_with_no_providers_simply_has_no_buttons(harness: Harness, configured: bool):
    """No implementations is the absence of the feature, not a degraded mode:
    every variable is still typeable by hand, which is the path that always
    works."""
    _with_providers(harness, *([_SapLogin()] if configured else []))
    iid = register_rca_item(harness.spec)
    body = harness.client.get(f"/a/rca/items/{iid}/env-providers").json()
    assert len(body["providers"]) == (1 if configured else 0)
