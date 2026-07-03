"""#307 — a flat, owner-managed logical Group, and the payoff: granting
`group:<id>` on a resource's Permission covers every current member (resolved via
`groups_of` → `Actor.groups` / `subjects_of`, so the read access_scope + the write
checker both honour it).

Tests drive the HTTP surface as different users through a mutable `holder["id"]`.
"""

from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection
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
        superusers=superusers,
    )
    return TestClient(app), spec


def _new_collection(spec: SpecStar, *, by: str, permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using(by):
        return rm.create(Collection(name="c", permission=permission)).resource_id


def _members(client: TestClient, gid: str) -> set[str]:
    return set(client.get(f"/groups/{gid}").json()["members"])


# ── Group CRUD + membership ──────────────────────────────────────────────────


def test_group_crud_and_membership_is_owner_managed():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = client.post("/groups", json={"name": "eng", "members": ["alice"]}).json()["resource_id"]
    assert _members(client, gid) == {"alice"}

    # owner adds a member (self is never added as a "member" — they're the manager)
    add = client.post(f"/groups/{gid}/members", json={"user_ids": ["carol", "bob"]})
    assert add.status_code == 204
    assert _members(client, gid) == {"alice", "carol"}

    # a member can list + read the group they're in, but not manage it
    holder["id"] = "alice"
    assert any(g["resource_id"] == gid for g in client.get("/groups").json())
    assert client.get(f"/groups/{gid}").status_code == 200
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["dan"]}).status_code == 403

    # a stranger can't see it at all (404, no existence leak) and it's not in their list
    holder["id"] = "zoe"
    assert client.get(f"/groups/{gid}").status_code == 404
    assert client.get("/groups").json() == []

    # owner removes a member + deletes the group
    holder["id"] = "bob"
    assert client.delete(f"/groups/{gid}/members/carol").status_code == 204
    assert _members(client, gid) == {"alice"}
    assert client.delete(f"/groups/{gid}").status_code == 204
    assert client.get(f"/groups/{gid}").status_code == 404


def test_a_stranger_cannot_manage_or_delete_a_group():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = client.post("/groups", json={"name": "eng"}).json()["resource_id"]
    holder["id"] = "zoe"  # can't even see it → 404 (not 403)
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["x"]}).status_code == 404
    assert client.delete(f"/groups/{gid}/members/x").status_code == 404
    assert client.delete(f"/groups/{gid}").status_code == 404


def test_a_superuser_can_read_and_manage_any_group():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder, superusers=frozenset({"root"}))
    gid = client.post("/groups", json={"name": "eng"}).json()["resource_id"]
    holder["id"] = "root"
    assert client.get(f"/groups/{gid}").status_code == 200  # superuser reads any group
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["alice"]}).status_code == 204
    assert _members(client, gid) == {"alice"}


def test_missing_group_is_404_everywhere():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    assert client.get("/groups/ghost").status_code == 404
    assert client.post("/groups/ghost/members", json={"user_ids": ["x"]}).status_code == 404
    assert client.delete("/groups/ghost/members/x").status_code == 404
    assert client.delete("/groups/ghost").status_code == 404


def test_membership_edits_are_idempotent_noops():
    """Adding an already-present member / removing a non-member is a 204 no-op (no
    spurious write)."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = client.post("/groups", json={"name": "eng", "members": ["alice"]}).json()["resource_id"]
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["alice"]}).status_code == 204
    assert client.delete(f"/groups/{gid}/members/nobody").status_code == 204
    assert _members(client, gid) == {"alice"}


# ── the payoff: a group grant covers its members ─────────────────────────────


def test_group_read_grant_on_a_collection_covers_members():
    """Granting `group:<id>` read_meta on a restricted collection makes it visible
    to every member (resolved at query time by the access_scope) and to nobody
    else."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    gid = client.post("/groups", json={"name": "eng", "members": ["alice"]}).json()["resource_id"]
    cid = _new_collection(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=[f"group:{gid}"])
    )
    holder["id"] = "alice"  # a member sees it via the group grant
    assert client.get(f"/collection/{cid}").status_code == 200
    holder["id"] = "carol"  # a non-member does not
    assert client.get(f"/collection/{cid}").status_code == 404


def test_group_write_grant_on_a_collection_lets_a_member_edit():
    """A `group:<id>` write_meta grant is honoured by the per-verb write checker's
    group-aware actor — a member (not the owner) can edit the collection."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    gid = client.post("/groups", json={"name": "eng", "members": ["alice"]}).json()["resource_id"]
    cid = _new_collection(
        spec,
        by="bob",
        permission=Permission(
            visibility="restricted",
            read_meta=[f"group:{gid}"],
            write_meta=[f"group:{gid}"],
        ),
    )
    holder["id"] = "alice"
    assert client.patch(f"/collection/{cid}", json={"name": "edited"}).status_code == 200
    holder["id"] = "carol"  # not in the group → the write is refused
    assert client.patch(f"/collection/{cid}", json={"name": "hijack"}).status_code in (403, 404)


def test_losing_group_membership_revokes_the_grant():
    """Membership is resolved live — removing a user from the group immediately
    drops their group-derived access to the collection."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    gid = client.post("/groups", json={"name": "eng", "members": ["alice"]}).json()["resource_id"]
    cid = _new_collection(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=[f"group:{gid}"])
    )
    holder["id"] = "alice"
    assert client.get(f"/collection/{cid}").status_code == 200
    holder["id"] = "bob"
    client.delete(f"/groups/{gid}/members/alice")
    holder["id"] = "alice"
    assert client.get(f"/collection/{cid}").status_code == 404  # grant gone with membership
