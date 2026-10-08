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

import datetime as dt

import msgspec
import pytest

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


async def test_browsing_lists_only_originals_with_their_fork_count_and_hides_the_unreadable(
    harness: Harness,
):
    """plan-skill-hub-ux-redo D2: browsing shows originals; a fork is a count
    on its original's row (GitHub's repo list), not a nested card."""
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage")
    fork = await _entry(hub, "bob", "triage", forked_from=root)
    await _entry(hub, "carol", "triage", forked_from=fork)  # a fork of a fork
    hidden = await _entry(hub, "alice", "hidden")
    _private(harness, hidden)

    res = harness.client.get("/skill-hub/entries")

    assert res.status_code == 200, res.text
    body = res.json()
    assert [e["id"] for e in body["entries"]] == [root]
    assert body["entries"][0]["fork_count"] == 1, "direct forks only"
    assert "forks" not in body["entries"][0]
    assert body["total"] == 1
    assert hidden not in res.text


async def test_a_search_lists_forks_beside_originals_each_saying_what_it_was_forked_from(
    harness: Harness,
):
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage")
    fork = await _entry(hub, "bob", "triage", forked_from=root)

    hits = harness.client.get("/skill-hub/entries", params={"q": "triage"}).json()

    by_id = {e["id"]: e for e in hits["entries"]}
    assert set(by_id) == {root, fork}
    assert by_id[fork]["forked_from"] == root and by_id[root]["forked_from"] == ""
    assert by_id[fork]["origin"] == {"owner": "alice", "name": "triage"}
    assert by_id[root]["origin"] is None
    assert by_id[root]["fork_count"] == 1
    assert hits["total"] == 2


async def test_the_owner_filter_lists_that_owners_skills_forks_included(harness: Harness):
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage")
    fork = await _entry(hub, "bob", "triage", forked_from=root)
    bobs = await _entry(hub, "bob", "deck")

    listed = harness.client.get("/skill-hub/entries", params={"owner": "bob"}).json()

    assert [e["id"] for e in listed["entries"]] == [bobs, fork]
    assert listed["total"] == 2


async def test_the_list_pages_with_offset_and_limit_and_says_the_total(harness: Harness):
    hub = _hub(harness)
    for name in ["a1", "a2", "a3", "a4", "a5"]:
        await _entry(hub, "alice", name)

    first = harness.client.get("/skill-hub/entries", params={"limit": 2}).json()
    third = harness.client.get("/skill-hub/entries", params={"limit": 2, "offset": 4}).json()

    assert [e["name"] for e in first["entries"]] == ["a1", "a2"]
    assert [e["name"] for e in third["entries"]] == ["a5"]
    assert first["total"] == third["total"] == 5


async def test_the_list_pages_fifty_at_a_time_by_default(harness: Harness):
    hub = _hub(harness)
    for n in range(51):
        await _entry(hub, "alice", f"s{n:02d}")

    listed = harness.client.get("/skill-hub/entries").json()

    assert len(listed["entries"]) == 50 and listed["total"] == 51


async def test_sorting_by_updated_puts_the_latest_content_first_and_unknown_last(
    harness: Harness,
):
    """`content_at` is when the content last changed (publish / rollback); a
    row written before it existed has none and sorts after every dated one."""
    hub = _hub(harness)
    at = {"t": dt.datetime(2026, 10, 1, tzinfo=dt.UTC)}
    hub._now = lambda: at["t"]  # noqa: SLF001 — the store's injected clock
    old = await _entry(hub, "alice", "old")
    at["t"] += dt.timedelta(days=1)
    new = await _entry(hub, "alice", "new")
    undated = await _entry(hub, "alice", "undated")
    rm = harness.spec.get_resource_manager(SkillHubEntry)
    rm.update(undated, msgspec.structs.replace(rm.get(undated).data, content_at=None))

    listed = harness.client.get("/skill-hub/entries", params={"sort": "updated"}).json()

    assert [e["id"] for e in listed["entries"]] == [new, old, undated]
    assert listed["entries"][0]["updated_at"].startswith("2026-10-02")
    assert listed["entries"][2]["updated_at"] is None


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
    fork = await _entry(hub, "bob", "triage", tools=["kb_search"], forked_from=root)

    asked = {"app": "rca", "q": "triage"}
    for_rca = {
        e["id"]: e for e in harness.client.get("/skill-hub/entries", params=asked).json()["entries"]
    }
    plain = harness.client.get("/skill-hub/entries", params={"q": "triage"}).json()["entries"]

    assert for_rca[root]["missing_tools"] == ["query_entity"]
    assert for_rca[fork]["missing_tools"] == ["kb_search"]
    assert [e["missing_tools"] for e in plain] == [[], []]


async def test_a_fork_whose_root_the_viewer_cannot_see_is_listed_on_its_own(harness: Harness):
    """Otherwise it would vanish with the root — and its owner could not find
    their own published skill on the page."""
    hub = _hub(harness)
    root = await _entry(hub, "alice", "triage")
    fork = await _entry(hub, "bob", "triage", forked_from=root)
    _private(harness, root)

    entries = harness.client.get("/skill-hub/entries").json()["entries"]

    assert [e["id"] for e in entries] == [fork]
    assert entries[0]["origin"] is None, "whose original it is is not the viewer's to know"


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
    assert [f["path"] for f in d["files"]] == ["SKILL.md", "references/g.md"]
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
    assert [e["id"] for e in entries] == [root] and entries[0]["fork_count"] == 0


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


async def test_an_unknown_app_in_the_diff_query_is_nothing_missing_not_a_500(harness: Harness):
    """Review round 1: `missing_tools_for` caught `KeyError`, but an unknown
    slug raises `FileNotFoundError` from the manifest loader — a 500 any
    signed-in user could trigger from a query string."""
    hub = _hub(harness)
    entry = await _entry(hub, "alice", "triage", tools=["exec"])

    for app in ("no-such-app", "../pm", ""):
        listed = harness.client.get("/skill-hub/entries", params={"app": app})
        detail = harness.client.get(f"/skill-hub/entries/{entry}", params={"app": app})
        assert listed.status_code == 200 and detail.status_code == 200, app
        assert listed.json()["entries"][0]["missing_tools"] == []
        assert detail.json()["missing_tools"] == []


async def test_an_entry_the_viewer_cannot_read_is_404_like_one_that_never_existed(harness: Harness):
    hub = _hub(harness)
    hidden = await _entry(hub, "alice", "hidden")
    _private(harness, hidden)
    gone = await _entry(hub, "alice", "gone")
    harness.spec.get_resource_manager(SkillHubEntry).delete(gone)

    codes = {
        harness.client.get(f"/skill-hub/entries/{e}").status_code for e in (hidden, gone, "nope")
    }
    bodies = [
        harness.client.get(f"/skill-hub/entries/{e}").json()["detail"]
        for e in (hidden, gone, "nope")
    ]

    assert codes == {404}
    # One code (D16), so a 404 never says 'exists, not for you'.
    assert bodies == [{"error": "not_found"}] * 3


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
    assert (row["is_copy"], row["upstream"], row["hub_entry"]) == (True, "live", entry)


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
    # D16: the fact (whose copy, which path), for the front end to word.
    assert res.json()["detail"] == {
        "error": "folder_in_the_way",
        "owner": "alice",
        "path": ".skill/triage/",
    }
    assert await harness.filestore.read(harness.iid, "/.skill/triage/SKILL.md") == _md("triage")


async def test_install_refuses_a_hand_written_folder_with_no_owner_to_name(harness: Harness):
    hub = _hub(harness)
    carols = await _entry(hub, "carol", "triage")
    await harness.filestore.write(
        harness.iid, "/.skill/triage/SKILL.md", b"---\nname: triage\n---\nmine\n"
    )

    res = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": carols})

    assert res.status_code == 409, res.text
    assert res.json()["detail"] == {
        "error": "folder_in_the_way",
        "owner": "",
        "path": ".skill/triage/",
    }


async def test_install_of_an_unreadable_entry_is_404_and_writes_nothing(harness: Harness):
    hub = _hub(harness)
    hidden = await _entry(hub, "alice", "hidden")
    _private(harness, hidden)

    res = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": hidden})

    assert res.status_code == 404
    assert await harness.filestore.ls(harness.iid, "/.skill/") == []


# ── the two guards that reddened nothing (veracity lens, round 1) ────────────


async def test_the_entry_model_has_no_auto_crud_route_so_nothing_can_put_around_the_review(
    harness: Harness,
):
    """`register_skill_hub` runs AFTER `spec.apply` so specstar emits no
    `/skill-hub-entry` routes — the docs say so, the code says so, and moving
    the registration one line up emitted 22 routes (a POST that creates an
    unreviewed row) with every test staying green. Pinned here."""
    paths = harness.spa_client.get("/api/openapi.json").json()["paths"]
    assert not [p for p in paths if "skill-hub-entry" in p], "auto-CRUD routes leaked"
    # The raw client (no route mapping): a POST on the leaked route would be a
    # 200 with a row; without it the address answers nothing useful.
    assert harness.spa_client.post("/api/skill-hub-entry", json={}).status_code >= 400


async def test_install_into_an_item_needs_edit_content_on_that_item(harness: Harness):
    """The panel's door writes `.skill/<name>/` into the item, the same
    standing instruction `install_skill` writes; the tool's verb is pinned by
    the authz parity table, the route's was pinned by nothing — swapping it
    for `read_content`, or dropping the check, reddened no test."""
    from workspace_app.apps.rca.model import RcaInvestigation

    hub = _hub(harness)
    entry = await _entry(hub, "alice", "triage")
    rm = harness.spec.get_resource_manager(RcaInvestigation)

    def item_with(*verbs: str) -> str:
        with rm.using("bob"):  # bob owns it; the harness user is a collaborator
            return rm.create(
                RcaInvestigation(
                    title="t",
                    owner="bob",
                    permission=Permission(
                        visibility="restricted",
                        read_meta=[f"user:{VIEWER}"],
                        **{v: [f"user:{VIEWER}"] for v in verbs},
                    ),
                )
            ).resource_id

    read_only = item_with("read_content", "converse")
    res = harness.client.post(f"/a/rca/items/{read_only}/skills/install", json={"entry_id": entry})
    assert res.status_code == 403, res.text
    assert await harness.filestore.ls(read_only, "/.skill/") == []

    editor = item_with("read_content", "edit_content")
    res = harness.client.post(f"/a/rca/items/{editor}/skills/install", json={"entry_id": entry})
    assert res.status_code == 200, res.text
    assert await harness.filestore.exists(editor, "/.skill/triage/SKILL.md")


async def test_the_detail_reads_the_skill_md_and_lists_the_rest_from_the_row(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
):
    """Review round 2: the detail read every blob (up to 20 MiB) to list file
    names and show one file. One read, for SKILL.md alone."""
    hub = _hub(harness)
    entry = await hub.publish(
        owner="alice",
        name="triage",
        description="d",
        source_item="i",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md("triage"), "assets/big.bin": b"x" * 200_000},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )
    reads: list[list[str] | None] = []
    real_read = hub.repos.read

    async def counting(entry_id: str, commit: str, *, paths=None) -> dict[str, bytes]:  # noqa: ANN001
        reads.append(sorted(paths) if paths is not None else None)
        return await real_read(entry_id, commit, paths=paths)

    monkeypatch.setattr(hub.repos, "read", counting)

    d = harness.client.get(f"/skill-hub/entries/{entry}").json()

    assert [f["path"] for f in d["files"]] == ["SKILL.md", "assets/big.bin"]
    assert d["files"][1]["size"] == 200_000, "the content's size, not a pointer's"
    assert d["skill_md"].startswith("---\nname: triage")
    assert reads == [["SKILL.md"]]


# ── counts (plan-skill-hub-history §4.8, U6) ─────────────────────────────────


async def test_the_list_and_the_detail_show_the_counts_and_sort_by_most_used(harness: Harness):
    hub = _hub(harness)
    quiet = await _entry(hub, "alice", "aaa")
    busy = await _entry(hub, "alice", "zzz")
    hub.usage.install(busy, user="bob", item="i1")
    hub.usage.use(busy, user="bob", item="i1")
    hub.usage.use(busy, user="carol", item="i2")
    hub.usage.install(quiet, user="bob", item="i1")
    await hub.usage.flush()

    listed = harness.client.get("/skill-hub/entries").json()
    by_name = {c["name"]: c for c in listed["entries"]}
    assert (by_name["zzz"]["installs"], by_name["zzz"]["uses"]) == (1, 2)
    assert (by_name["aaa"]["installs"], by_name["aaa"]["uses"]) == (1, 0)
    assert listed["counted_since"], "the counts say since when"
    assert [c["name"] for c in listed["entries"]] == ["aaa", "zzz"]
    popular = harness.client.get("/skill-hub/entries", params={"sort": "popular"}).json()
    assert [c["name"] for c in popular["entries"]] == ["zzz", "aaa"]

    detail = harness.client.get(f"/skill-hub/entries/{busy}").json()
    assert (detail["installs"], detail["uses"]) == (1, 2)
    assert detail["counted_since"] == listed["counted_since"]


async def test_installing_from_the_skills_panel_counts(
    harness: Harness,
):
    hub = _hub(harness)
    entry = await _entry(hub, "alice", "triage")

    assert (
        harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry}).status_code
        == 200
    )
    await hub.usage.flush()

    assert hub.usage.totals([entry]) == {entry: (1, 0)}


async def test_refreshing_a_copy_that_lost_its_version_record_is_a_409_with_the_way_out(
    harness: Harness,
):
    """Review round 1 (defect #9): a `.origin` naming a version the repo does
    not have raised out of the refresh route as a 500."""
    hub = _hub(harness)
    entry = await _entry(hub, "alice", "triage")
    assert (
        harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry}).status_code
        == 200
    )
    path = "/.skill/triage/.origin"
    origin = msgspec.json.decode(await harness.filestore.read(harness.iid, path))
    origin["commit"] = "0" * 40
    await harness.filestore.write(harness.iid, path, msgspec.json.encode(origin))
    await _entry(hub, "alice", "triage", description="v2")

    res = harness.client.post(harness.wpath("/skills/triage/refresh"), json={"force": False})

    assert res.status_code == 409, res.text
    assert "reset" in res.json()["detail"]
