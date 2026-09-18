"""The skill hub's read routes and the item-side install door (plan P6).

`GET /skill-hub/entries` is the page's list (roots with forks beneath, `q`
search, `mine`); `GET /skill-hub/entries/{id}` the detail (with `?app=` for
the tool diff the install告知 shows; the owner also gets the full
`permission` for the share dialog). There is no hub-side download: D2/Q8 put
every download in the item, where the installed copy's zip already exists.
`POST /a/{slug}/items/{id}/skills/install` is what the Skills panel's install
button calls — the same core the `install_skill` tool uses, so the two doors
cannot drift. Visibility is the viewer's on every one of them (Q10: an entry
you may not read is 404, the same 404 as one that never existed).
"""

from __future__ import annotations

import msgspec

from workspace_app.apps.skill_hub import SkillHubEntry, SkillHubReview, SkillHubStore
from workspace_app.perm import Permission

from .conftest import Harness

VIEWER = "default-user"  # make_spec()'s default user — what the harness's requests run as


def _md(name: str, description: str = "d") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# body of {name}\n".encode()


def _hub(harness: Harness) -> SkillHubStore:
    return harness.spa_client.app.state.skill_hub  # ty: ignore[unresolved-attribute]


async def _entry(
    hub: SkillHubStore,
    owner: str,
    name: str,
    *,
    description: str = "d",
    tools: list[str] | None = None,
    forked_from: str = "",
    app: str = "rca",
    notes: list[str] | None = None,
) -> str:
    return await hub.publish(
        owner=owner,
        name=name,
        description=description,
        source_item="inv-src",
        source_app=app,
        source_profile="default",
        payload={"SKILL.md": _md(name, description), "references/g.md": b"g"},
        referenced_tools=tools or [],
        review=SkillHubReview(verdict="notes" if notes else "ok", notes=notes or [], model="m"),
        forked_from=forked_from,
    )


def _private(harness: Harness, entry_id: str) -> None:
    rm = harness.spec.get_resource_manager(SkillHubEntry)
    rm.update(
        entry_id,
        msgspec.structs.replace(rm.get(entry_id).data, permission=Permission(visibility="private")),
    )


# ── list ─────────────────────────────────────────────────────────────────────


async def test_the_list_nests_forks_under_their_root_and_hides_what_the_viewer_cannot_read(
    harness: Harness,
):
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage")
    fork = await _entry(hub, "bob", "triage", forked_from=root)
    hidden = await _entry(hub, "alice", "hidden")
    _private(harness, hidden)

    res = harness.client.get("/skill-hub/entries")

    assert res.status_code == 200, res.text
    entries = res.json()["entries"]
    assert [e["id"] for e in entries] == [root]
    assert [f["id"] for f in entries[0]["forks"]] == [fork]
    assert entries[0]["forks"][0]["forked_from"] == root
    assert hidden not in res.text


async def test_the_list_searches_name_and_description_and_filters_mine(harness: Harness):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER, "my-reflow", description="Solder.")
    theirs = await _entry(hub, "alice", "log-digger", description="reflow logs")
    other = await _entry(hub, "alice", "deck", description="slides")

    q = harness.client.get("/skill-hub/entries", params={"q": "REFLOW"}).json()["entries"]
    assert sorted(e["id"] for e in q) == sorted([mine, theirs])

    only_mine = harness.client.get("/skill-hub/entries", params={"mine": "true"}).json()["entries"]
    assert [e["id"] for e in only_mine] == [mine]
    assert only_mine[0]["is_mine"] is True
    assert other not in [e["id"] for e in q]


async def test_the_list_computes_the_tool_diff_per_row_for_the_app_asked_about(harness: Harness):
    """The Skills panel's picker lists for ONE item, so it asks with that
    item's App and every row — forks included — says what that App lacks."""
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage", tools=["exec", "query_entity"], app="pm")
    await _entry(hub, "bob", "triage", tools=["kb_search"], forked_from=root)

    for_rca = harness.client.get("/skill-hub/entries", params={"app": "rca"}).json()["entries"]
    plain = harness.client.get("/skill-hub/entries").json()["entries"]

    assert for_rca[0]["missing_tools"] == ["query_entity"]
    assert for_rca[0]["forks"][0]["missing_tools"] == ["kb_search"]
    assert plain[0]["missing_tools"] == [] and plain[0]["forks"][0]["missing_tools"] == []


async def test_a_fork_whose_root_the_viewer_cannot_see_is_listed_on_its_own(harness: Harness):
    """Otherwise it would vanish with the root — and its owner could not find
    their own published skill on the page."""
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage")
    fork = await _entry(hub, "bob", "triage", forked_from=root)
    _private(harness, root)

    entries = harness.client.get("/skill-hub/entries").json()["entries"]

    assert [e["id"] for e in entries] == [fork]


# ── detail ───────────────────────────────────────────────────────────────────


async def test_the_detail_carries_the_body_the_files_the_review_and_the_lineage(harness: Harness):
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage", tools=["exec"])
    fork = await _entry(hub, "bob", "triage", forked_from=root, notes=["n1"])

    res = harness.client.get(f"/skill-hub/entries/{fork}")

    assert res.status_code == 200, res.text
    d = res.json()
    assert d["owner"] == "bob" and d["name"] == "triage"
    assert d["skill_md"].startswith("---\nname: triage")
    assert d["files"] == ["SKILL.md", "references/g.md"]
    assert d["review"] == {"verdict": "notes", "notes": ["n1"], "model": "m"}
    assert d["forked_from"] == {"entry": root, "state": "live", "owner": "alice", "name": "triage"}
    assert d["is_owner"] is False
    assert d["source_item"] == "", "where it was made is the owner's business"

    root_detail = harness.client.get(f"/skill-hub/entries/{root}").json()
    assert [f["id"] for f in root_detail["forks"]] == [fork]
    assert root_detail["forked_from"] is None


async def test_a_deleted_fork_is_not_listed_under_its_root(harness: Harness):
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage")
    fork = await _entry(hub, "bob", "triage", forked_from=root)
    harness.spec.get_resource_manager(SkillHubEntry).delete(fork)

    assert harness.client.get(f"/skill-hub/entries/{root}").json()["forks"] == []
    entries = harness.client.get("/skill-hub/entries").json()["entries"]
    assert [e["id"] for e in entries] == [root] and entries[0]["forks"] == []


async def test_the_owner_sees_the_source_item_and_is_owner(harness: Harness):
    hub = _hub(harness)
    mine = await _entry(hub, VIEWER, "triage")

    d = harness.client.get(f"/skill-hub/entries/{mine}").json()

    assert d["is_owner"] is True and d["source_item"] == "inv-src"
    assert d["permission"]["visibility"] == "public" and d["permission"]["read_content"] == []


async def test_a_non_owner_does_not_get_the_permission_object(harness: Harness):
    hub = _hub(harness)
    theirs = await _entry(hub, "alice", "triage")

    assert harness.client.get(f"/skill-hub/entries/{theirs}").json()["permission"] is None


async def test_the_lineage_reports_a_root_that_went_private_or_was_deleted(harness: Harness):
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage")
    fork = await _entry(hub, "bob", "triage", forked_from=root)
    _private(harness, root)
    assert harness.client.get(f"/skill-hub/entries/{fork}").json()["forked_from"] == {
        "entry": root,
        "state": "unpublished",
        "owner": "",
        "name": "",
    }

    harness.spec.get_resource_manager(SkillHubEntry).delete(root)
    assert harness.client.get(f"/skill-hub/entries/{fork}").json()["forked_from"]["state"] == (
        "deleted"
    )


async def test_the_detail_computes_the_tool_diff_for_the_app_asked_about(harness: Harness):
    hub = _hub(harness)
    entry = await _entry(hub, "alice", "triage", tools=["exec", "query_entity"], app="pm")

    for_rca = harness.client.get(f"/skill-hub/entries/{entry}", params={"app": "rca"}).json()
    for_pm = harness.client.get(f"/skill-hub/entries/{entry}", params={"app": "pm"}).json()
    plain = harness.client.get(f"/skill-hub/entries/{entry}").json()

    assert for_rca["missing_tools"] == ["query_entity"]
    assert for_pm["missing_tools"] == []
    assert plain["missing_tools"] == []


async def test_an_entry_the_viewer_cannot_read_is_404_like_one_that_never_existed(harness: Harness):
    hub = _hub(harness)
    hidden = await _entry(hub, "alice", "hidden")
    _private(harness, hidden)
    gone = await _entry(hub, "alice", "gone")
    harness.spec.get_resource_manager(SkillHubEntry).delete(gone)

    codes = {
        harness.client.get(f"/skill-hub/entries/{e}").status_code for e in (hidden, gone, "nope")
    }
    bodies = {
        harness.client.get(f"/skill-hub/entries/{e}").json()["detail"]
        for e in (hidden, gone, "nope")
    }

    assert codes == {404}
    assert len(bodies) == 1, "one wording, so a 404 never says 'exists, not for you'"


# ── install (the panel's door) ───────────────────────────────────────────────


async def test_install_puts_the_copy_in_the_item_and_names_the_missing_tools(harness: Harness):
    hub = _hub(harness)
    entry = await _entry(hub, "alice", "triage", tools=["exec", "query_entity"], app="pm")

    res = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry})

    assert res.status_code == 200, res.text
    assert res.json() == {"name": "triage", "missing_tools": ["query_entity"]}
    assert await harness.filestore.read(harness.iid, "/.skill/triage/references/g.md") == b"g"
    row = next(
        s
        for s in harness.client.get(harness.wpath("/skills")).json()["skills"]
        if s["name"] == "triage"
    )
    assert (row["is_copy"], row["upstream"]) == (True, "live")


async def test_install_refuses_to_overwrite_and_says_whose_copy_is_there(harness: Harness):
    hub = _hub(harness)
    alices = await _entry(hub, "alice", "triage")
    carols = await _entry(hub, "carol", "triage")
    assert (
        harness.client.post(harness.wpath("/skills/install"), json={"entry_id": alices}).status_code
        == 200
    )

    res = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": carols})

    assert res.status_code == 409, res.text
    assert "alice" in res.json()["detail"]
    assert await harness.filestore.read(harness.iid, "/.skill/triage/SKILL.md") == _md("triage")


async def test_install_of_an_unreadable_entry_is_404_and_writes_nothing(harness: Harness):
    hub = _hub(harness)
    hidden = await _entry(hub, "alice", "hidden")
    _private(harness, hidden)

    res = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": hidden})

    assert res.status_code == 404
    assert await harness.filestore.ls(harness.iid, "/.skill/") == []
