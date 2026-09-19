"""The skill hub's management routes — owner-only, on the detail page (plan P7).

Unpublish = `visibility: private` (the entry vanishes from every other
viewer's list, search and install; copies already made stay); republish = back
to public. Delete = soft, final (Q5/Q10: copies and forks read "deleted"; a
re-publish of the name starts a new entry). Transfer = the `owner` field moves
and NOTHING else does — the id is the identity copies and forks point at, so
they must all survive. Permission = the same `PermissionBody` every other
resource's share UI sends. A non-owner gets 403 on all of them, and an entry
the viewer may not read gets the 404 every read route gives (Q10).
"""

from __future__ import annotations

import msgspec

from workspace_app.apps.skill_hub import SkillHubEntry, SkillHubReview, SkillHubStore
from workspace_app.apps.skills import install_hub_skill, skill_upstream
from workspace_app.files import WorkspaceFiles
from workspace_app.perm import Permission

from .conftest import Harness

VIEWER = "default-user"  # make_spec()'s default user — what the harness's requests run as


def _md(name: str) -> bytes:
    return f"---\nname: {name}\ndescription: d\n---\n\nbody\n".encode()


def _hub(harness: Harness) -> SkillHubStore:
    return harness.spa_client.app.state.skill_hub  # ty: ignore[unresolved-attribute]


async def _entry(
    hub: SkillHubStore, owner: str, name: str = "triage", forked_from: str = ""
) -> str:
    return await hub.publish(
        owner=owner,
        name=name,
        description="d",
        source_item="inv-src",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md(name)},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
        forked_from=forked_from,
    )


def _entry_row(harness: Harness, entry_id: str) -> SkillHubEntry:
    data = harness.spec.get_resource_manager(SkillHubEntry).get(entry_id).data
    assert isinstance(data, SkillHubEntry)
    return data


# ── who may ──────────────────────────────────────────────────────────────────


async def test_every_management_route_is_403_for_a_non_owner_and_changes_nothing(
    harness: Harness,
):
    hub = _hub(harness)
    theirs = await _entry(hub, "alice")
    before = _entry_row(harness, theirs)

    calls = [
        harness.client.post(f"/skill-hub/entries/{theirs}/unpublish"),
        harness.client.post(f"/skill-hub/entries/{theirs}/republish"),
        harness.client.delete(f"/skill-hub/entries/{theirs}"),
        harness.client.post(f"/skill-hub/entries/{theirs}/transfer", json={"owner": "bob"}),
        harness.client.put(
            f"/skill-hub/entries/{theirs}/permission", json={"visibility": "private"}
        ),
    ]

    assert [c.status_code for c in calls] == [403] * 5, [c.text for c in calls]
    assert _entry_row(harness, theirs) == before
    assert hub.get(theirs) is not None


async def test_every_management_refusal_is_a_code_the_front_end_translates(harness: Harness):
    """plan-skill-hub-ui-polish D16: the routes' person-facing refusals used
    to be English sentences shown as-is inside a zh-TW page. They are codes
    now — the site's existing shape (`turn_gate` quota codes, this hub's own
    edit `reason`s) — with the parameters a sentence needs, and the front
    end words them. One test per code."""
    hub = _hub(harness)
    theirs = await _entry(hub, "alice")
    mine = await _entry(hub, VIEWER)
    bobs = await _entry(hub, "bob")

    owner_only = harness.client.post(f"/skill-hub/entries/{theirs}/unpublish")
    assert (owner_only.status_code, owner_only.json()["detail"]) == (403, {"error": "owner_only"})

    empty = harness.client.post(f"/skill-hub/entries/{mine}/transfer", json={"owner": ""})
    assert (empty.status_code, empty.json()["detail"]) == (
        400,
        {"error": "transfer_owner_required"},
    )

    taken = harness.client.post(f"/skill-hub/entries/{mine}/transfer", json={"owner": "bob"})
    assert (taken.status_code, taken.json()["detail"]) == (
        409,
        {"error": "transfer_name_taken", "owner": "bob", "name": _entry_row(harness, bobs).name},
    )


async def test_an_entry_the_viewer_cannot_read_is_404_on_management_too(harness: Harness):
    hub = _hub(harness)
    hidden = await _entry(hub, "alice")
    rm = harness.spec.get_resource_manager(SkillHubEntry)
    rm.update(
        hidden,
        msgspec.structs.replace(rm.get(hidden).data, permission=Permission(visibility="private")),
    )

    assert harness.client.post(f"/skill-hub/entries/{hidden}/unpublish").status_code == 404
    assert harness.client.delete(f"/skill-hub/entries/{hidden}").status_code == 404


# ── unpublish / republish ────────────────────────────────────────────────────


async def test_unpublish_hides_the_entry_from_others_and_republish_brings_it_back(
    harness: Harness,
):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)

    res = harness.client.post(f"/skill-hub/entries/{mine}/unpublish")

    assert res.status_code == 200, res.text
    assert res.json()["visibility"] == "private"
    assert hub.state_for(mine, "bob") == ("unpublished", None)
    assert hub.state_for(mine, VIEWER)[0] == "live", "the owner still sees it"
    # …and it is still on the owner's own page.
    mine_list = harness.client.get("/skill-hub/entries", params={"mine": "true"}).json()
    assert [e["id"] for e in mine_list["entries"]] == [mine]

    res = harness.client.post(f"/skill-hub/entries/{mine}/republish")

    assert res.status_code == 200 and res.json()["visibility"] == "public"
    assert hub.state_for(mine, "bob")[0] == "live"


async def test_unpublish_keeps_the_grant_lists_so_republish_loses_nothing(harness: Harness):
    """Unpublish is a visibility flip, not a reset: a restricted entry's
    invite list survives a take-down. Republish sets `public`, which is the
    plan's "重新上架"; a restricted republish is a `permission` PUT."""
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)
    harness.client.put(
        f"/skill-hub/entries/{mine}/permission",
        json={"visibility": "restricted", "read_content": ["user:bob"]},
    )

    harness.client.post(f"/skill-hub/entries/{mine}/unpublish")

    perm = _entry_row(harness, mine).permission
    assert (perm.visibility, perm.read_content) == ("private", ["user:bob"])


async def test_an_installed_copy_is_untouched_by_unpublish_and_reads_the_state(harness: Harness):
    hub = _hub(harness)
    alices = await _entry(hub, VIEWER)  # the harness user publishes…
    files = WorkspaceFiles(harness.filestore)
    await install_hub_skill(files, harness.iid, hub, alices)  # …and bob's item installs it
    harness.client.post(f"/skill-hub/entries/{alices}/unpublish")

    assert await harness.filestore.read(harness.iid, "/.skill/triage/SKILL.md") == _md("triage")
    up = await skill_upstream(files, harness.iid, "rca", "default", "triage", hub=hub, viewer="bob")
    assert up is not None and up.state == "unpublished"


# ── delete ───────────────────────────────────────────────────────────────────


async def test_delete_is_soft_and_final_for_everything_that_points_at_it(harness: Harness):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)
    fork = await _entry(hub, "bob", forked_from=mine)
    files = WorkspaceFiles(harness.filestore)
    await install_hub_skill(files, harness.iid, hub, mine)

    res = harness.client.delete(f"/skill-hub/entries/{mine}")

    assert res.status_code == 204, res.text
    assert hub.state_for(mine, VIEWER) == ("deleted", None), "gone for the owner too"
    assert harness.client.get("/skill-hub/entries", params={"mine": "true"}).json()["entries"] == []
    assert harness.client.get(f"/skill-hub/entries/{fork}").json()["forked_from"]["state"] == (
        "deleted"
    )
    up = await skill_upstream(files, harness.iid, "rca", "default", "triage", hub=hub, viewer="bob")
    assert up is not None and up.state == "deleted"
    assert await harness.filestore.read(harness.iid, "/.skill/triage/SKILL.md") == _md("triage")


async def test_deleting_twice_is_404_the_second_time(harness: Harness):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)
    assert harness.client.delete(f"/skill-hub/entries/{mine}").status_code == 204

    assert harness.client.delete(f"/skill-hub/entries/{mine}").status_code == 404


async def test_delete_frees_the_entrys_files(harness: Harness):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)
    assert await hub.payload_of(mine) != {}

    harness.client.delete(f"/skill-hub/entries/{mine}")

    assert await hub.payload_of(mine) == {}


# ── transfer ─────────────────────────────────────────────────────────────────


async def test_transfer_moves_the_owner_and_nothing_else_so_copies_and_forks_survive(
    harness: Harness,
):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)
    fork = await _entry(hub, "carol", forked_from=mine)
    files = WorkspaceFiles(harness.filestore)
    await install_hub_skill(files, harness.iid, hub, mine)
    before = _entry_row(harness, mine)

    res = harness.client.post(f"/skill-hub/entries/{mine}/transfer", json={"owner": "bob"})

    assert res.status_code == 200, res.text
    assert res.json()["owner"] == "bob"
    after = _entry_row(harness, mine)
    assert after == msgspec.structs.replace(before, owner="bob")
    assert hub.find("bob", "triage") == mine and hub.find(VIEWER, "triage") is None
    assert harness.client.get(f"/skill-hub/entries/{fork}").json()["forked_from"]["owner"] == "bob"
    up = await skill_upstream(files, harness.iid, "rca", "default", "triage", hub=hub, viewer="x")
    assert up is not None and up.state == "live"
    # The previous owner is now a non-owner: the very next management call is 403.
    assert harness.client.post(f"/skill-hub/entries/{mine}/unpublish").status_code == 403


async def test_transfer_refuses_a_name_the_new_owner_already_publishes(harness: Harness):
    """`(owner, name)` is the identity; two rows with one identity would make
    `find` — and every re-publish — ambiguous."""
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)
    bobs = await _entry(hub, "bob")

    res = harness.client.post(f"/skill-hub/entries/{mine}/transfer", json={"owner": "bob"})

    assert res.status_code == 409, res.text
    assert _entry_row(harness, mine).owner == VIEWER and hub.get(bobs) is not None


async def test_transfer_needs_a_real_target(harness: Harness):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)

    assert (
        harness.client.post(f"/skill-hub/entries/{mine}/transfer", json={"owner": ""}).status_code
        == 400
    )
    assert (
        harness.client.post(
            f"/skill-hub/entries/{mine}/transfer", json={"owner": VIEWER}
        ).status_code
        == 400
    )


# ── permission ───────────────────────────────────────────────────────────────


async def test_permission_put_is_the_same_body_as_every_other_share_ui(harness: Harness):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)

    res = harness.client.put(
        f"/skill-hub/entries/{mine}/permission",
        json={"visibility": "restricted", "read_content": ["user:bob", "group:g1"]},
    )

    assert res.status_code == 200, res.text
    assert res.json() == {"resource_id": mine, "visibility": "restricted", "notified": []}
    assert hub.state_for(mine, "bob")[0] == "live"
    assert hub.state_for(mine, "carol")[0] == "unpublished"
    assert harness.client.get(f"/skill-hub/entries/{mine}").json()["visibility"] == "restricted"


async def test_permission_put_rejects_a_bad_visibility(harness: Harness):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER)

    res = harness.client.put(f"/skill-hub/entries/{mine}/permission", json={"visibility": "secret"})

    assert res.status_code == 400
