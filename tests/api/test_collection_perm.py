"""#262 PR2 — collection access control: the list is filtered to what the
caller may see, single-resource access is gated, and owners set permissions.

Tests drive the HTTP surface as different users via a mutable `holder["id"]`.
"""

import datetime as dt

import msgspec
from msgspec import UNSET
from specstar import SpecStar
from specstar.permission import PermissionResult
from specstar.types import ResourceAction

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.perm import Permission
from workspace_app.perm.checker import CollectionPermissionChecker, _patch_touches_permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection
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
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _set_permission(spec: SpecStar, cid: str, permission: Permission, *, by: str = "bob") -> None:
    rm = spec.get_resource_manager(Collection)
    coll = rm.get(cid).data
    assert isinstance(coll, Collection)
    with rm.using(by, dt.datetime.now(dt.UTC)):
        rm.update(cid, msgspec.structs.replace(coll, permission=permission))


def _names(client: TestClient) -> set[str]:
    return {c["name"] for c in client.get("/kb/collections").json()}


def test_list_collections_hides_a_private_collection_from_a_non_owner():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    # bob owns a collection and locks it down to private.
    secret = client.post("/kb/collections", json={"name": "secret"}).json()["resource_id"]
    _set_permission(spec, secret, Permission(visibility="private"))
    # alice owns a public (default) one.
    holder["id"] = "alice"
    client.post("/kb/collections", json={"name": "open"})
    # alice's list shows the public one but not bob's private one.
    names = _names(client)
    assert "open" in names
    assert "secret" not in names


def test_single_collection_get_is_hidden_from_a_non_owner():
    """specstar's access_scope makes a private collection a uniform 404 on the
    auto-CRUD `GET /collection/{id}` (the FE's primary single-resource path) —
    not just the hand-written list."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    secret = client.post("/kb/collections", json={"name": "secret"}).json()["resource_id"]
    _set_permission(spec, secret, Permission(visibility="private"))
    assert client.get(f"/collection/{secret}").status_code == 200  # the owner reads it
    holder["id"] = "alice"
    assert client.get(f"/collection/{secret}").status_code == 404  # hidden from others


def test_auto_crud_delete_blocked_for_in_scope_non_owner():
    """A restricted collection alice CAN read (granted read_meta, so in scope —
    not 404'd) still can't be DELETEd by her via the auto-CRUD route: the
    permission_checker gates delete to the owner (403)."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, Permission(visibility="restricted", read_meta=["user:alice"]))
    holder["id"] = "alice"
    assert client.get(f"/collection/{cid}").status_code == 200  # in scope (read_meta)
    assert client.delete(f"/collection/{cid}").status_code == 403  # but not the owner


def _restricted(**grants: list[str]) -> Permission:
    """A restricted Permission with the named grant lists (e.g.
    `_restricted(read_meta=["user:alice"], add_content=["user:alice"])`)."""
    return Permission(visibility="restricted", **{k: list(v) for k, v in grants.items()})


def test_patch_edit_blocked_for_in_scope_non_writer():
    """The FE edits a collection via `PATCH /collection/{id}`. An in-scope member
    (read_meta) who lacks write_meta is 403'd by the per-verb checker."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"]))
    holder["id"] = "alice"
    assert client.patch(f"/collection/{cid}", json={"name": "hijacked"}).status_code == 403


def test_patch_edit_allowed_for_write_meta_grantee():
    """A member granted write_meta (and read_meta, to be in scope) can edit."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"], write_meta=["user:alice"]))
    holder["id"] = "alice"
    assert client.patch(f"/collection/{cid}", json={"name": "renamed"}).status_code == 200
    # confirm the edit landed (read the envelope back as the still-in-scope alice)
    assert client.get(f"/collection/{cid}").json()["data"]["name"] == "renamed"


def test_patch_can_set_auto_digest():
    """The 'auto-generate cards' settings toggle sets ``auto_digest`` via the same
    ``PATCH /collection/{id}`` the FE uses for other field edits — no dedicated
    endpoint (unlike superuser-only ``is_global``). The owner flips it and it
    round-trips through the auto-CRUD read."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    assert client.get(f"/collection/{cid}").json()["data"]["auto_digest"] is False  # default off

    assert client.patch(f"/collection/{cid}", json={"auto_digest": True}).status_code == 200
    assert client.get(f"/collection/{cid}").json()["data"]["auto_digest"] is True
    # …and it surfaces in the /kb/collections list (CollectionOut) the FE reads to
    # reflect the toggle — guards against a pydantic response-model field drop.
    row = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == cid)
    assert row["auto_digest"] is True


def test_patch_cannot_rewire_permission_without_change_permission():
    """write_meta lets a member edit fields, but NOT rewrite the access-control
    object — a PATCH that names `permission` needs change_permission (403)."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"], write_meta=["user:alice"]))
    holder["id"] = "alice"
    resp = client.patch(f"/collection/{cid}", json={"permission": {"visibility": "public"}})
    assert resp.status_code == 403


def test_permanently_delete_blocked_for_in_scope_non_owner():
    """The FE delete button hits `DELETE /collection/{id}/permanently`; an
    in-scope non-owner is 403'd, the owner succeeds."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"], write_meta=["user:alice"]))
    holder["id"] = "alice"
    assert client.delete(f"/collection/{cid}/permanently").status_code == 403
    holder["id"] = "bob"
    assert client.delete(f"/collection/{cid}/permanently").status_code in (200, 204)


def test_owner_can_edit_and_delete_a_restricted_collection():
    """Regression guard: the owner is never blocked by the per-verb checker."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "mine"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"]))
    assert client.patch(f"/collection/{cid}", json={"name": "renamed"}).status_code == 200
    assert client.delete(f"/collection/{cid}/permanently").status_code in (200, 204)


# ─────────────────── item 1: the permission-set endpoint ───────────────────


def test_owner_sets_collection_private_via_endpoint_then_hidden():
    """The setter makes access_scope LIVE: owner PUTs visibility=private, then a
    non-owner is 404'd on the single GET and the collection drops out of the list."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "secret"}).json()["resource_id"]
    resp = client.put(f"/kb/collections/{cid}/permission", json={"visibility": "private"})
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "private"
    holder["id"] = "alice"
    assert client.get(f"/collection/{cid}").status_code == 404
    assert "secret" not in _names(client)


def test_non_owner_cannot_set_permission():
    """An in-scope member without change_permission is 403'd by the setter."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"]))
    holder["id"] = "alice"
    resp = client.put(f"/kb/collections/{cid}/permission", json={"visibility": "public"})
    assert resp.status_code == 403


def test_change_permission_grantee_can_set_permission():
    """A delegate granted change_permission (and read_meta, to see it) may rewire
    access — even though they hold no write_meta (the setter persists as owner)."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(
        spec, cid, _restricted(read_meta=["user:alice"], change_permission=["user:alice"])
    )
    holder["id"] = "alice"
    resp = client.put(
        f"/kb/collections/{cid}/permission",
        json={"visibility": "restricted", "read_meta": ["user:carol"]},
    )
    assert resp.status_code == 200


def test_set_permission_notifies_newly_granted_users():
    """Newly-granted users get a `share` notification (the actor doesn't)."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "team"}).json()["resource_id"]
    resp = client.put(
        f"/kb/collections/{cid}/permission",
        json={"visibility": "restricted", "read_meta": ["user:alice", "user:bob"]},
    )
    assert resp.json()["notified"] == ["alice"]  # bob is the actor → not notified
    holder["id"] = "alice"
    kinds = [n["kind"] for n in client.get("/notifications").json()]
    assert "share" in kinds


def test_set_permission_rejects_unknown_visibility():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "x"}).json()["resource_id"]
    assert (
        client.put(f"/kb/collections/{cid}/permission", json={"visibility": "open"}).status_code
        == 400
    )


def test_set_permission_unknown_collection_is_404():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    assert (
        client.put("/kb/collections/nope/permission", json={"visibility": "private"}).status_code
        == 404
    )


def test_get_permission_returns_the_current_state():
    """#310 — the share dialog pre-fills from `GET …/permission` (the full grant
    lists so it can map each grantee to a role)."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "x"}).json()["resource_id"]
    _set_permission(
        spec,
        cid,
        Permission(visibility="restricted", read_meta=["user:alice"], read_content=["user:alice"]),
    )
    state = client.get(f"/kb/collections/{cid}/permission").json()
    assert state["visibility"] == "restricted"
    assert state["read_meta"] == ["user:alice"]
    assert state["read_content"] == ["user:alice"]
    assert state["edit_content"] == []


def test_get_permission_defaults_to_public_when_absent():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "x"}).json()["resource_id"]
    state = client.get(f"/kb/collections/{cid}/permission").json()
    assert state["visibility"] == "public"
    assert state["read_meta"] == []


def test_get_permission_is_hidden_from_a_non_owner():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "secret"}).json()["resource_id"]
    _set_permission(spec, cid, Permission(visibility="private"))
    holder["id"] = "alice"
    assert client.get(f"/kb/collections/{cid}/permission").status_code == 404


# ─────────────────── item 3: content-route guards ───────────────────


def _txt():
    return {"file": ("a.txt", b"hello world", "text/plain")}


def test_upload_blocked_for_in_scope_non_member():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"]))  # read but no add_content
    holder["id"] = "alice"
    assert client.post(f"/kb/collections/{cid}/documents", files=_txt()).status_code == 403


def test_upload_allowed_for_add_content_grantee():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"], add_content=["user:alice"]))
    holder["id"] = "alice"
    assert client.post(f"/kb/collections/{cid}/documents", files=_txt()).status_code == 200


def test_sync_blocked_for_non_editor():
    """The edit_content guard fires BEFORE the git_url check, so a non-editor is
    403'd whether or not the collection is a code repo."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"]))
    holder["id"] = "alice"
    assert client.post(f"/kb/collections/{cid}/sync").status_code == 403


def test_reindex_blocked_for_non_editor():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"]))
    holder["id"] = "alice"
    assert client.post(f"/kb/collections/{cid}/reindex").status_code == 403


def test_wiki_write_blocked_for_non_editor():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "shared"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"]))
    holder["id"] = "alice"
    resp = client.put(f"/kb/collections/{cid}/wiki/page?path=/index.md", content=b"# hi")
    assert resp.status_code == 403


def test_content_route_hides_a_private_collection_as_404():
    """A non-member who can't even `read_meta` a private collection gets 404 from
    a content route (no existence leak), not 403."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "secret"}).json()["resource_id"]
    _set_permission(spec, cid, Permission(visibility="private"))
    holder["id"] = "alice"
    assert client.post(f"/kb/collections/{cid}/documents", files=_txt()).status_code == 404


def test_content_routes_pass_for_owner_on_a_restricted_collection():
    """Regression guard: the owner is never blocked by the content guards."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "mine"}).json()["resource_id"]
    _set_permission(spec, cid, _restricted(read_meta=["user:alice"]))
    assert client.post(f"/kb/collections/{cid}/documents", files=_txt()).status_code == 200
    assert client.post(f"/kb/collections/{cid}/reindex").status_code == 200


# ─── checker unit tests (direct — branches specstar never drives via HTTP) ───


class _Snap:
    """A stand-in for specstar's loaded `current_resource` (data + meta)."""

    def __init__(self, owner: str, perm: Permission | None) -> None:
        self.data = Collection(name="c", permission=perm)
        self.meta = type("Meta", (), {"created_by": owner})()


class _Ctx:
    """A minimal permission context — attributes are only set when provided, so
    `getattr(ctx, "current_resource", UNSET)` etc. behave like the real structs."""

    def __init__(self, action, *, user="alice", snap=UNSET, data=UNSET, patch_data=UNSET) -> None:
        self.action = action
        self.user = user
        if snap is not UNSET:
            self.current_resource = snap
        if data is not UNSET:
            self.data = data
        if patch_data is not UNSET:
            self.patch_data = patch_data


def test_checker_denies_write_when_current_resource_absent():
    """Defensive: a write with no loaded snapshot can't be authorized → deny."""
    ck = CollectionPermissionChecker()
    assert ck.check_permission(_Ctx(ResourceAction.update)) == PermissionResult.deny
    assert ck.check_permission(_Ctx(ResourceAction.delete)) == PermissionResult.deny


def test_checker_requests_no_parts_for_reads():
    assert CollectionPermissionChecker().required_resource_parts(ResourceAction.get) == frozenset()


def test_checker_modify_without_a_body_is_not_a_permission_rewrite():
    """A `modify` may carry no `data`; that's a plain write_meta check, not a
    permission rewrite — a write_meta grantee is allowed."""
    ck = CollectionPermissionChecker()
    snap = _Snap("bob", Permission(visibility="restricted", write_meta=["user:alice"]))
    assert ck.check_permission(_Ctx(ResourceAction.modify, snap=snap)) == PermissionResult.allow


def test_patch_touches_permission_across_flavors():
    from jsonpatch import JsonPatch

    perm_op = JsonPatch([{"op": "replace", "path": "/permission/visibility", "value": "public"}])
    name_op = JsonPatch([{"op": "replace", "path": "/name", "value": "x"}])
    assert _patch_touches_permission(perm_op) is True  # RFC 6902 → /permission path
    assert _patch_touches_permission(name_op) is False  # RFC 6902 → unrelated path
    assert _patch_touches_permission({"permission": {}}) is True  # RFC 7386 merge (dict)
    assert _patch_touches_permission(object()) is False  # neither flavor


def test_superuser_sees_every_collection():
    """A configured superuser's access scope is UNRESTRICTED — they read a
    private collection a normal user is 404'd from."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder, superusers=frozenset({"root"}))
    secret = client.post("/kb/collections", json={"name": "secret"}).json()["resource_id"]
    _set_permission(spec, secret, Permission(visibility="private"))
    holder["id"] = "alice"
    assert client.get(f"/collection/{secret}").status_code == 404  # ordinary non-owner
    holder["id"] = "root"
    assert client.get(f"/collection/{secret}").status_code == 200  # superuser
