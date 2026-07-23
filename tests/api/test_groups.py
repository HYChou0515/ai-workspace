"""#307 + #608 — org-canonical Groups.

A Group is created by a SUPERUSER who designates an owner (#608). The owner
manages members + maintainers, can transfer ownership, and can delete; a
maintainer manages MEMBERS only; a superuser bypasses everything. Granting
`group:<id>` on a resource's Permission then covers every current member
(resolved via `groups_of` → `Actor.groups` / `subjects_of`).

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

ROOT = frozenset({"root"})


def _client_and_spec(
    holder: dict[str, str], *, superusers: frozenset[str] = ROOT
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


def _mk(
    client: TestClient,
    holder: dict[str, str],
    *,
    owner: str,
    name: str = "eng",
    members: list[str] | None = None,
) -> str:
    """Create a group AS the superuser `root`, designating `owner`, then restore
    the acting user. Groups are superuser-created in the org-canonical model."""
    prev = holder["id"]
    holder["id"] = "root"
    gid = client.post(
        "/groups", json={"name": name, "owner": owner, "members": members or []}
    ).json()["resource_id"]
    holder["id"] = prev
    return gid


def _new_collection(spec: SpecStar, *, by: str, permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using(by):
        return rm.create(Collection(name="c", permission=permission)).resource_id


def _members(client: TestClient, gid: str) -> set[str]:
    return set(client.get(f"/groups/{gid}").json()["members"])


# ── #608 governance: who may create / delegate / transfer ────────────────────


def test_only_a_superuser_creates_a_group():
    holder = {"id": "alice"}
    client, _ = _client_and_spec(holder)
    # a plain user can no longer create their own group (org-canonical model)
    assert client.post("/groups", json={"name": "rogue"}).status_code == 403
    holder["id"] = "root"
    assert client.post("/groups", json={"name": "eng"}).status_code == 200


def test_superuser_designates_an_owner_who_then_manages_members():
    holder = {"id": "root"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    # bob (the designated owner, NOT the creator root) manages membership
    holder["id"] = "bob"
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["carol"]}).status_code == 204
    assert _members(client, gid) == {"alice", "carol"}
    # the group shows up in bob's own /groups list (owner-by-field, not created_by)
    assert any(g["resource_id"] == gid for g in client.get("/groups").json())


def test_owner_delegates_to_a_maintainer_who_manages_members_only():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    # owner delegates member-management to dave
    assert client.post(f"/groups/{gid}/maintainers", json={"user_ids": ["dave"]}).status_code == 204
    holder["id"] = "dave"
    # a maintainer manages MEMBERS...
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["erin"]}).status_code == 204
    assert _members(client, gid) == {"alice", "erin"}
    # ...but cannot delegate further, transfer, or delete (owner-only)
    assert client.post(f"/groups/{gid}/maintainers", json={"user_ids": ["zoe"]}).status_code == 403
    assert client.put(f"/groups/{gid}/owner", json={"owner": "dave"}).status_code == 403
    assert client.delete(f"/groups/{gid}").status_code == 403


def test_a_plain_member_cannot_manage_maintainers():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    holder["id"] = "alice"  # a member sees the group (403 to manage), not a stranger (404)
    assert client.post(f"/groups/{gid}/maintainers", json={"user_ids": ["x"]}).status_code == 403
    holder["id"] = "zoe"
    assert client.post(f"/groups/{gid}/maintainers", json={"user_ids": ["x"]}).status_code == 404


def test_owner_can_transfer_ownership():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    assert client.put(f"/groups/{gid}/owner", json={"owner": "dave"}).status_code == 200
    # the old owner is now unrelated to the group — 404 (no existence leak), not 403
    assert client.delete(f"/groups/{gid}").status_code == 404
    # the new owner has it
    holder["id"] = "dave"
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["frank"]}).status_code == 204
    assert client.delete(f"/groups/{gid}").status_code == 204


def test_maintainer_removal_and_read_visibility():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob")
    client.post(f"/groups/{gid}/maintainers", json={"user_ids": ["dave"]})
    # a maintainer may READ the group they help manage
    holder["id"] = "dave"
    assert client.get(f"/groups/{gid}").status_code == 200
    assert any(g["resource_id"] == gid for g in client.get("/groups").json())
    # owner removes the maintainer → dave loses management
    holder["id"] = "bob"
    assert client.delete(f"/groups/{gid}/maintainers/dave").status_code == 204
    holder["id"] = "dave"
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["x"]}).status_code == 404


def test_a_superuser_manages_maintainers_and_transfer_on_any_group():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob")
    holder["id"] = "root"
    assert client.post(f"/groups/{gid}/maintainers", json={"user_ids": ["dave"]}).status_code == 204
    assert client.put(f"/groups/{gid}/owner", json={"owner": "carol"}).status_code == 200


# ── membership management (owner or maintainer) ──────────────────────────────


def test_member_read_and_stranger_hidden():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    # a member reads + lists the group, but can't manage it
    holder["id"] = "alice"
    assert client.get(f"/groups/{gid}").status_code == 200
    assert any(g["resource_id"] == gid for g in client.get("/groups").json())
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["dan"]}).status_code == 403
    # a stranger can't see it at all (404, no existence leak) and it's not in their list
    holder["id"] = "zoe"
    assert client.get(f"/groups/{gid}").status_code == 404
    assert client.get("/groups").json() == []


def test_owner_edits_and_deletes():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["carol"]}).status_code == 204
    assert _members(client, gid) == {"alice", "carol"}
    assert client.delete(f"/groups/{gid}/members/carol").status_code == 204
    assert _members(client, gid) == {"alice"}
    assert client.delete(f"/groups/{gid}").status_code == 204
    assert client.get(f"/groups/{gid}").status_code == 404


def test_missing_group_is_404_everywhere():
    holder = {"id": "root"}
    client, _ = _client_and_spec(holder)
    assert client.get("/groups/ghost").status_code == 404
    assert client.post("/groups/ghost/members", json={"user_ids": ["x"]}).status_code == 404
    assert client.delete("/groups/ghost/members/x").status_code == 404
    assert client.post("/groups/ghost/maintainers", json={"user_ids": ["x"]}).status_code == 404
    assert client.delete("/groups/ghost/maintainers/x").status_code == 404
    assert client.put("/groups/ghost/owner", json={"owner": "x"}).status_code == 404
    assert client.delete("/groups/ghost").status_code == 404


def test_membership_edits_are_idempotent_noops():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    assert client.post(f"/groups/{gid}/members", json={"user_ids": ["alice"]}).status_code == 204
    assert client.delete(f"/groups/{gid}/members/nobody").status_code == 204
    assert _members(client, gid) == {"alice"}


# ── #608 P3: /me groups, the pickable list, work-item group scope ────────────


def test_me_returns_the_callers_group_ids():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    holder["id"] = "alice"  # a member's /me carries the group id (for FE gating)
    assert gid in client.get("/me").json()["groups"]
    holder["id"] = "carol"  # a non-member's does not
    assert gid not in client.get("/me").json().get("groups", [])


def test_pickable_lists_every_group_with_counts_but_never_member_ids():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", name="eng", members=["alice", "carol"])
    # a total stranger to the group can still pick it (org-canonical: grant to anyone)
    holder["id"] = "zoe"
    rows = client.get("/groups/pickable").json()
    row = next(r for r in rows if r["resource_id"] == gid)
    assert row["name"] == "eng"
    assert row["member_count"] == 2
    assert "members" not in row  # the actual member ids are never exposed here


def test_group_grant_on_a_work_item_is_resolved_in_list_scope():
    from workspace_app.apps.rca.model import RcaInvestigation

    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        iid = rm.create(
            RcaInvestigation(
                title="t",
                owner="bob",
                permission=Permission(visibility="restricted", read_meta=[f"group:{gid}"]),
            )
        ).resource_id
    holder["id"] = "alice"  # member via the group → the item shows in her list
    ids = {e["revision_info"]["resource_id"] for e in client.get("/rca-investigation").json()}
    assert iid in ids
    holder["id"] = "carol"  # non-member → hidden
    ids = {e["revision_info"]["resource_id"] for e in client.get("/rca-investigation").json()}
    assert iid not in ids


# ── the payoff: a group grant covers its members ─────────────────────────────


def test_group_read_grant_on_a_collection_covers_members():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    cid = _new_collection(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=[f"group:{gid}"])
    )
    holder["id"] = "alice"  # a member sees it via the group grant
    assert client.get(f"/collection/{cid}").status_code == 200
    holder["id"] = "carol"  # a non-member does not
    assert client.get(f"/collection/{cid}").status_code == 404


def test_group_write_grant_on_a_collection_lets_a_member_edit():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
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
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    gid = _mk(client, holder, owner="bob", members=["alice"])
    cid = _new_collection(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=[f"group:{gid}"])
    )
    holder["id"] = "alice"
    assert client.get(f"/collection/{cid}").status_code == 200
    holder["id"] = "bob"
    client.delete(f"/groups/{gid}/members/alice")
    holder["id"] = "alice"
    assert client.get(f"/collection/{cid}").status_code == 404  # grant gone with membership
