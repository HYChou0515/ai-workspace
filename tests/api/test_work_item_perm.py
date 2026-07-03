"""#306 — App WorkItem access control: a restricted item is hidden (404) from a
non-member on the auto-CRUD route, gated writes are 403, and the owner sets the
permission. The same shared `Permission` + `authorize()` that governs collections
(#262 / #300), applied to every App's WorkItem via `apps.registry`.

Tests drive the HTTP surface as different users through a mutable `holder["id"]`.
"""

import datetime as dt

import msgspec
from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _client_and_spec(
    holder: dict[str, str], *, superusers: frozenset[str] = frozenset()
) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _new_item(spec: SpecStar, *, by: str, permission: Permission | None = None) -> str:
    """Create an rca WorkItem directly via the resource manager as `by` (so
    `created_by` = the owner), optionally already carrying a permission."""
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(RcaInvestigation(title="t", owner=by, permission=permission)).resource_id


def _set_item_permission(spec: SpecStar, iid: str, permission: Permission, *, by: str) -> None:
    rm = spec.get_resource_manager(RcaInvestigation)
    item = rm.get(iid).data
    assert isinstance(item, RcaInvestigation)
    with rm.using(by, dt.datetime.now(dt.UTC)):
        rm.update(iid, msgspec.structs.replace(item, permission=permission))


def test_private_item_is_hidden_from_a_non_owner_on_auto_crud():
    """specstar's access_scope makes a private item a uniform 404 on the auto-CRUD
    `GET /rca-investigation/{id}` (the FE's single-item path), not just a list."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _new_item(spec, by="bob", permission=Permission(visibility="private"))
    assert client.get(f"/rca-investigation/{iid}").status_code == 200  # the owner reads it
    holder["id"] = "alice"
    assert client.get(f"/rca-investigation/{iid}").status_code == 404  # hidden from others


def test_list_hides_a_private_item_from_a_non_owner():
    """The auto-CRUD list (`GET /rca-investigation`, what the dashboard reads) is
    access_scope-filtered: a non-owner sees public items but not a private one."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    secret = _new_item(spec, by="bob", permission=Permission(visibility="private"))
    holder["id"] = "alice"
    open_item = _new_item(spec, by="alice")  # alice's own, public (default)
    ids = {e["revision_info"]["resource_id"] for e in client.get("/rca-investigation").json()}
    assert open_item in ids
    assert secret not in ids


def test_restricted_item_write_is_gated_by_write_meta_and_delete_by_owner():
    """An in-scope member (read_meta) who lacks write_meta can read the item but
    the per-verb checker 403s their PATCH; delete stays owner-only."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _new_item(spec, by="bob")
    _set_item_permission(
        spec, iid, Permission(visibility="restricted", read_meta=["user:alice"]), by="bob"
    )
    holder["id"] = "alice"
    assert client.get(f"/rca-investigation/{iid}").status_code == 200  # in scope (read_meta)
    assert client.patch(f"/rca-investigation/{iid}", json={"title": "hijacked"}).status_code == 403
    assert client.delete(f"/rca-investigation/{iid}").status_code == 403  # not the owner


def test_write_meta_grantee_can_edit_the_item():
    """A member granted write_meta (and read_meta, to be in scope) edits the item."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _new_item(spec, by="bob")
    _set_item_permission(
        spec,
        iid,
        Permission(visibility="restricted", read_meta=["user:alice"], write_meta=["user:alice"]),
        by="bob",
    )
    holder["id"] = "alice"
    assert client.patch(f"/rca-investigation/{iid}", json={"title": "fixed"}).status_code == 200


def test_close_is_gated_on_the_item_permission():
    """Closing (even a pure workspace release, which does no `rm.update` so the
    write checker never fires) must be route-guarded: an in-scope member without
    write_meta is 403, a stranger is 404, the owner closes (204)."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _new_item(spec, by="bob")
    _set_item_permission(
        spec, iid, Permission(visibility="restricted", read_meta=["user:alice"]), by="bob"
    )
    holder["id"] = "alice"  # in scope (read_meta) but no write_meta
    assert client.post(f"/a/rca/items/{iid}/close", json={}).status_code == 403
    holder["id"] = "carol"  # cannot even see it
    assert client.post(f"/a/rca/items/{iid}/close", json={}).status_code == 404
    holder["id"] = "bob"  # owner
    assert client.post(f"/a/rca/items/{iid}/close", json={}).status_code == 204


def test_owner_sets_item_permission_and_a_non_owner_cannot():
    """`PUT /a/{slug}/items/{id}/permission` — the owner tightens the item and the
    newly-granted user is notified; the change takes effect (a stranger is then
    404'd); a granted-but-not-change_permission member can't rewire it."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _new_item(spec, by="bob")
    r = client.put(
        f"/a/rca/items/{iid}/permission",
        json={"visibility": "restricted", "read_meta": ["user:alice"]},
    )
    assert r.status_code == 200
    assert r.json()["visibility"] == "restricted"
    assert r.json()["notified"] == ["alice"]
    holder["id"] = "carol"  # not granted → now hidden
    assert client.get(f"/rca-investigation/{iid}").status_code == 404
    holder["id"] = "alice"  # in scope but only read_meta → can't change permission
    assert (
        client.put(f"/a/rca/items/{iid}/permission", json={"visibility": "public"}).status_code
        == 403
    )


def test_setter_rejects_an_invalid_visibility():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _new_item(spec, by="bob")
    r = client.put(f"/a/rca/items/{iid}/permission", json={"visibility": "bogus"})
    assert r.status_code == 400


def test_item_routes_404_for_an_unknown_item():
    """The item route-guard resolves the item first — an unknown id is a 404
    (both the setter and close funnel through `_authorize_item`)."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    assert (
        client.put("/a/rca/items/nope/permission", json={"visibility": "public"}).status_code == 404
    )
    assert client.post("/a/rca/items/nope/close", json={}).status_code == 404


def test_setter_404_for_an_unknown_app():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    r = client.put("/a/bogusapp/items/x/permission", json={"visibility": "public"})
    assert r.status_code == 404
