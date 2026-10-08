"""Clearing the sign-ins people left in single items (`docs/plan-personal-env.md`, D10).

Before "my environment variables", signing in wrote the token into that one
item's personal values — and an item's own value still wins a name (D4). So the
token a person signs in for again on their page would be shadowed, in every
item they had signed in to before, by the old one. Nothing clears those
automatically (D5); the operator runs this when they choose.

It removes only names a deploy sign-in produces — read from the providers the
API actually loaded, not a hand-kept list — and only in items whose policy for
that name is Private first / Private only, where the old value shadows my
environment variables — and only for a person who holds that name there too,
since until they sign in again the item's value is the only one their tools
have (review round 2, F1). In a Shared item the old value is the one the item
USES (it never reads my environment variables), so it stays (review round 1,
F1).
Never shows a value. Dry run by default. Superusers only: it writes everyone's
rows.
"""

from __future__ import annotations

from fastapi import FastAPI

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.private_env import PrivateEnvStore
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .test_env_providers import _SapLogin

URL = "/admin/env/clear-item-sign-ins"


def _world():
    holder = {"id": "root"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=frozenset({"root"}),
    )
    assert isinstance(app, FastAPI)
    app.state.env_providers = [_SapLogin()]  # produces SAP_TOKEN, SAP_HOST
    rm = spec.get_resource_manager(PlaygroundItem)

    def item(policy: dict[str, str]) -> str:
        made = PlaygroundItem(title="t", owner="root", profile="echo", env_policy=policy)
        return rm.create(made).resource_id

    a = item({"SAP_TOKEN": "private_first", "SAP_HOST": "private_only"})  # asks for mine
    b = item({})  # Shared: uses what is stored in it
    store = PrivateEnvStore(spec)
    store.replace("alice", a, {"SAP_TOKEN": "old-a", "MY_KEY": "typed"})
    store.replace("alice", b, {"SAP_HOST": "h"})
    store.replace("bob", a, {"OTHER": "o"})
    store.record_seam("alice", a, {"SAP_TOKEN": "from-sso"})
    # Alice has signed in again on her page — so the old item value hides it.
    store.replace_personal("alice", {"SAP_TOKEN": "fresh", "SAP_HOST": "fresh-h"})
    # Carol has not: the value in the item is the only one her tools have.
    store.replace("carol", a, {"SAP_TOKEN": "only-copy"})
    return TestClient(app), holder, store, a, b


def test_a_dry_run_lists_what_would_go_and_changes_nothing():
    client, _, store, a, b = _world()

    resp = client.post(URL, json={"apply": False})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] is False
    assert [(r["user_id"], r["item_id"], r["names"]) for r in body["rows"]] == [
        ("alice", a, ["SAP_TOKEN"]),
    ]
    assert "old-a" not in resp.text and "from-sso" not in resp.text
    assert store.get("alice", a) == {"SAP_TOKEN": "old-a", "MY_KEY": "typed"}


def test_applying_removes_only_the_sign_in_names():
    client, _, store, a, b = _world()

    assert client.post(URL, json={"apply": True}).json()["applied"] is True

    assert store.get("alice", a) == {"MY_KEY": "typed"}
    assert store.get("bob", a) == {"OTHER": "o"}
    # What the deploy's SSO said is not a sign-in someone left: untouched.
    assert store.seam("alice", a) == {"SAP_TOKEN": "from-sso"}


def test_a_shared_item_keeps_the_value_it_uses():
    """Round 1, F1: a Shared item never reads my environment variables, so the
    value stored in it is the one its tools get. Clearing it would break it."""
    client, _, store, a, b = _world()

    client.post(URL, json={"apply": True})

    assert store.get("alice", b) == {"SAP_HOST": "h"}


def test_a_value_that_hides_nothing_stays():
    """Round 2 (F1): until a person signs in on their page, the value in the
    item is the only one their tools have — removing it would break them."""
    client, _, store, a, b = _world()

    client.post(URL, json={"apply": True})

    assert store.get("carol", a) == {"SAP_TOKEN": "only-copy"}


def test_a_second_run_finds_nothing():
    client, _, _, a, b = _world()
    client.post(URL, json={"apply": True})

    assert client.post(URL, json={"apply": False}).json()["rows"] == []


def test_only_a_superuser_may_run_it():
    client, holder, store, a, b = _world()
    holder["id"] = "alice"

    assert client.post(URL, json={"apply": True}).status_code == 403
    assert store.get("alice", a)["SAP_TOKEN"] == "old-a"


def _script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[2] / "scripts" / "clear_item_sign_ins.py"
    spec = importlib.util.spec_from_file_location("_clear_item_sign_ins", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_dry_runs_by_default_and_never_prints_a_value(capsys):
    client, _, store, a, b = _world()

    assert _script().run(client, "", apply=False) == 0

    out = capsys.readouterr().out
    assert f"alice\t{a}\tSAP_TOKEN" in out
    assert "would remove 1 name(s) in 1" in out
    assert "old-a" not in out
    assert store.get("alice", a)["SAP_TOKEN"] == "old-a"


def test_the_script_applies_when_asked(capsys):
    client, _, store, a, b = _world()

    assert _script().run(client, "", apply=True) == 0

    assert "removed 1 name(s)" in capsys.readouterr().out
    assert store.get("alice", a) == {"MY_KEY": "typed"}


def test_the_script_says_why_it_was_refused(capsys):
    client, holder, _, a, b = _world()
    holder["id"] = "alice"

    assert _script().run(client, "", apply=True) == 1

    assert "server.superusers" in capsys.readouterr().err


def test_the_script_can_carry_who_it_runs_as():
    """Behind a gateway the identity is a header or cookie the deploy reads; the
    script must be able to send it, or a superuser cannot run it (round 1)."""
    script = _script()

    assert script.parse_headers(["X-Forwarded-User: root", "Cookie: sso=abc; x=1"]) == {
        "X-Forwarded-User": "root",
        "Cookie": "sso=abc; x=1",
    }


def test_the_names_are_the_ones_this_deploy_signs_in_for():
    """Not a list kept by hand: change the deploy's sign-ins, change the names."""
    client, _, store, a, b = _world()
    app = client.app
    assert isinstance(app, FastAPI)
    app.state.env_providers = []

    assert client.post(URL, json={"apply": True}).json()["rows"] == []
    assert store.get("alice", a)["SAP_TOKEN"] == "old-a"
