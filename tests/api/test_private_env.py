"""Viewer login (`docs/plan-wui-viewer-login.md`) — the PRIVATE layer's storage.

A person's own values for one item: stored on the server (a turn re-run on
another pod after this one died has no browser to ask), keyed by (person, item)
so a token logged in for one item never reaches another item's tools, and
readable by that person alone. The routes address the CALLER's row only — there
is no user parameter — so nobody, a superuser included, can name someone
else's.
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _world(**kw):
    """A client acting as whoever `holder["id"]` names, and an item bob owns that
    alice may open (read_meta) — the lowest grant that shows her the item."""
    holder = {"id": "bob"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        **kw,
    )
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        rid = rm.create(
            RcaInvestigation(
                title="t",
                owner="bob",
                permission=Permission(visibility="restricted", read_meta=["user:alice"]),
            )
        ).resource_id
    return TestClient(app), holder, rid, spec


def _url(rid: str) -> str:
    return f"/a/rca/items/{rid}/env/private"


def test_a_person_reads_back_what_they_stored():
    client, holder, rid, _ = _world()
    holder["id"] = "alice"

    assert client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}}).status_code == 200

    assert client.get(_url(rid)).json() == {"values": {"ERP_TOKEN": "a-1"}}


def test_nobody_else_reads_it_not_even_the_items_owner():
    """The row is addressed as "mine": bob asking the same URL gets HIS row
    (empty), not alice's. There is no parameter by which to ask for hers."""
    client, holder, rid, _ = _world()
    holder["id"] = "alice"
    client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}})

    holder["id"] = "bob"

    assert client.get(_url(rid)).json() == {"values": {}}


def test_a_superuser_cannot_read_it_either():
    client, holder, rid, _ = _world(superusers=frozenset({"root"}))
    holder["id"] = "alice"
    client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}})

    holder["id"] = "root"

    assert client.get(_url(rid)).json() == {"values": {}}


def test_an_item_the_caller_cannot_open_stores_nothing():
    client, holder, rid, spec = _world()
    holder["id"] = "mallory"

    r = client.put(_url(rid), json={"values": {"X": "1"}})

    assert r.status_code in (403, 404)
    from workspace_app.api.private_env import PrivateEnvStore

    assert PrivateEnvStore(spec).get("mallory", rid) == {}


def test_there_is_no_raw_resource_route_to_list_everyones_rows():
    """Registered after `spec.apply`, so specstar emitted no auto-CRUD for it —
    the caller-scoped routes above are its only door."""
    client, holder, rid, _ = _world(superusers=frozenset({"root"}))
    holder["id"] = "alice"
    client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}})
    holder["id"] = "root"

    paths = {getattr(r, "path", "") for r in client.app.routes}

    assert not any("private-env" in p for p in paths)


def test_logging_out_forgets_the_row():
    client, holder, rid, spec = _world()
    holder["id"] = "alice"
    client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}})

    assert client.delete(_url(rid)).status_code == 204

    assert client.get(_url(rid)).json() == {"values": {}}


def test_logging_out_needs_no_access_to_the_item():
    """Deleting your own row is never gated: someone who has lost access to the
    item must still be able to take their credential back out."""
    client, holder, rid, spec = _world()
    from workspace_app.api.private_env import PrivateEnvStore

    PrivateEnvStore(spec).replace("mallory", rid, {"X": "1"})
    holder["id"] = "mallory"

    assert client.delete(_url(rid)).status_code == 204
    assert PrivateEnvStore(spec).get("mallory", rid) == {}


# ─── what writes it: the deploy's IRequestEnv, on every request (P4) ───────


def test_a_senders_request_env_is_kept_in_their_private_row():
    """`plan-wui-viewer-login` Q4(b): what the deploy's `env_for` says about a
    person is written to THEIR private row, so a turn with no request behind it
    later (a re-run on another pod) still has it."""
    from workspace_app.api.private_env import PrivateEnvStore

    from .test_request_env import CookieEnv, _send_app

    client, _runner, item_id, spec = _send_app(CookieEnv())
    client.cookies.set("sso", "abc")

    with client:
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert PrivateEnvStore(spec).get("u", item_id) == {"SSO": "abc", "CALLER": "u"}


def test_a_value_the_person_typed_reaches_their_turn_with_no_seam_configured():
    """The private layer is not a feature of `IRequestEnv`: a deploy with no seam
    still has people who typed their own value for an item."""
    from workspace_app.api.private_env import PrivateEnvStore

    from .test_request_env import _send_app

    client, runner, item_id, spec = _send_app(None, env_vars={"FROM_ITEM": "i"})
    PrivateEnvStore(spec).replace("u", item_id, {"MINE": "x"})

    with client:
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert runner.envs == [{"MINE": "x", "FROM_ITEM": "i"}]


def test_the_latest_request_overwrites_only_the_names_it_carries():
    """Last write wins, per name: the seam re-writes what it provides on every
    request; a name it does not provide (typed by hand) is left alone."""
    from workspace_app.api.private_env import PrivateEnvStore

    from .test_request_env import CookieEnv, _send_app

    client, runner, item_id, spec = _send_app(CookieEnv())
    PrivateEnvStore(spec).replace("u", item_id, {"SSO": "stale", "TYPED": "t"})
    client.cookies.set("sso", "fresh")

    with client:
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert PrivateEnvStore(spec).get("u", item_id) == {"SSO": "fresh", "TYPED": "t", "CALLER": "u"}
    assert runner.envs[-1]["SSO"] == "fresh"
    assert runner.envs[-1]["TYPED"] == "t"
