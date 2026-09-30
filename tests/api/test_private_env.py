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
