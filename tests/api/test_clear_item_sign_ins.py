"""Clearing the sign-ins people left in single items (`docs/plan-personal-env.md`, D10).

Before "my environment variables", signing in wrote the token into that one
item's personal values — and an item's own value still wins a name (D4). So the
token a person signs in for again on their page would be shadowed, in every
item they had signed in to before, by the old one. Nothing clears those
automatically (D5); the operator runs this when they choose.

It removes only names a deploy sign-in produces — read from the providers the
API actually loaded, not a hand-kept list — and never shows a value. Dry run by
default. Superusers only: it writes everyone's rows.
"""

from __future__ import annotations

from fastapi import FastAPI

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.private_env import PrivateEnvStore
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
    store = PrivateEnvStore(spec)
    store.replace("alice", "item-a", {"SAP_TOKEN": "old-a", "MY_KEY": "typed"})
    store.replace("alice", "item-b", {"SAP_HOST": "h"})
    store.replace("bob", "item-a", {"OTHER": "o"})
    store.record_seam("alice", "item-a", {"SAP_TOKEN": "from-sso"})
    return TestClient(app), holder, store


def test_a_dry_run_lists_what_would_go_and_changes_nothing():
    client, _, store = _world()

    resp = client.post(URL, json={"apply": False})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] is False
    assert sorted((r["user_id"], r["item_id"], r["names"]) for r in body["rows"]) == [
        ("alice", "item-a", ["SAP_TOKEN"]),
        ("alice", "item-b", ["SAP_HOST"]),
    ]
    assert "old-a" not in resp.text and "from-sso" not in resp.text
    assert store.get("alice", "item-a") == {"SAP_TOKEN": "old-a", "MY_KEY": "typed"}


def test_applying_removes_only_the_sign_in_names():
    client, _, store = _world()

    assert client.post(URL, json={"apply": True}).json()["applied"] is True

    assert store.get("alice", "item-a") == {"MY_KEY": "typed"}
    assert store.get("alice", "item-b") == {}
    assert store.get("bob", "item-a") == {"OTHER": "o"}
    # What the deploy's SSO said is not a sign-in someone left: untouched.
    assert store.seam("alice", "item-a") == {"SAP_TOKEN": "from-sso"}


def test_a_second_run_finds_nothing():
    client, _, _ = _world()
    client.post(URL, json={"apply": True})

    assert client.post(URL, json={"apply": False}).json()["rows"] == []


def test_only_a_superuser_may_run_it():
    client, holder, store = _world()
    holder["id"] = "alice"

    assert client.post(URL, json={"apply": True}).status_code == 403
    assert store.get("alice", "item-b") == {"SAP_HOST": "h"}


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
    client, _, store = _world()

    assert _script().run(client, "", apply=False) == 0

    out = capsys.readouterr().out
    assert "alice\titem-a\tSAP_TOKEN" in out
    assert "would remove 2 name(s) in 2" in out
    assert "old-a" not in out
    assert store.get("alice", "item-a")["SAP_TOKEN"] == "old-a"


def test_the_script_applies_when_asked(capsys):
    client, _, store = _world()

    assert _script().run(client, "", apply=True) == 0

    assert "removed 2 name(s)" in capsys.readouterr().out
    assert store.get("alice", "item-b") == {}


def test_the_script_says_why_it_was_refused(capsys):
    client, holder, _ = _world()
    holder["id"] = "alice"

    assert _script().run(client, "", apply=True) == 1

    assert "server.superusers" in capsys.readouterr().err


def test_the_names_are_the_ones_this_deploy_signs_in_for():
    """Not a list kept by hand: change the deploy's sign-ins, change the names."""
    client, _, store = _world()
    app = client.app
    assert isinstance(app, FastAPI)
    app.state.env_providers = []

    assert client.post(URL, json={"apply": True}).json()["rows"] == []
    assert store.get("alice", "item-a")["SAP_TOKEN"] == "old-a"
