"""What the skill hub's skill page reads beyond the detail
(docs/plan-skill-hub-ux-redo.md P2): version numbers on the timeline (D8),
file sizes and the script count for the files tab (D12), where the viewer
has the skill installed (D3), and what installing into each of an App's
workspaces would do (D6)."""

from __future__ import annotations

import msgspec

from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.skill_hub import SkillHubEntry, SkillHubReview, SkillHubStore
from workspace_app.apps.skills import SkillOrigin
from workspace_app.perm import Permission

from .conftest import Harness

VIEWER = "default-user"


def _md(body: str, name: str = "triage") -> bytes:
    return f"---\nname: {name}\ndescription: d\n---\n{body}\n".encode()


def _hub(harness: Harness) -> SkillHubStore:
    return harness.spa_client.app.state.skill_hub  # ty: ignore[unresolved-attribute]


async def _publish(
    hub: SkillHubStore,
    body: str,
    *,
    owner: str = "alice",
    name: str = "triage",
    extra: dict[str, bytes] | None = None,
    tools: list[str] | None = None,
) -> str:
    return await hub.publish(
        owner=owner,
        name=name,
        description=body,
        source_item="inv-src",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md(body, name), **(extra or {})},
        referenced_tools=tools or [],
        review=SkillHubReview(verdict="ok"),
    )


def _revision(harness: Harness, entry_id: str) -> str:
    return harness.spec.get_resource_manager(SkillHubEntry).get(entry_id).info.revision_id


def _item(harness: Harness, title: str, *verbs: str, owner: str = "bob") -> str:
    """An rca workspace `owner` made, shared with the viewer for `verbs`."""
    rm = harness.spec.get_resource_manager(RcaInvestigation)
    with rm.using(owner):
        return rm.create(
            RcaInvestigation(
                title=title,
                owner=owner,
                permission=Permission(
                    visibility="restricted",
                    read_meta=[f"user:{VIEWER}"],
                    **{v: [f"user:{VIEWER}"] for v in verbs},
                ),
            )
        ).resource_id


async def _copy(harness: Harness, item: str, name: str, entry: str) -> None:
    """A copy of `entry` sitting in `item`, as an install leaves it."""
    await harness.filestore.write(item, f"/.skill/{name}/SKILL.md", _md("x", name))
    origin = SkillOrigin(source="hub", files={}, entry=entry, commit="c")
    await harness.filestore.write(item, f"/.skill/{name}/.origin", msgspec.json.encode(origin))


# ── version numbers (D8) ─────────────────────────────────────────────────────


async def test_publishes_and_rollbacks_are_numbered_in_order_and_other_rows_are_not(
    harness: Harness,
):
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    first = _revision(harness, entry)
    await _publish(hub, "two")
    await hub.transfer(entry, VIEWER)
    master = hub.get(entry)
    assert master is not None
    await hub.rollback(entry, first, expected=master.commit)

    events = harness.client.get(f"/skill-hub/entries/{entry}/history").json()["events"]

    assert [(e["kind"], e["version"]) for e in events] == [
        ("rollback", 3),
        ("transfer", None),
        ("publish", 2),
        ("publish", 1),
    ]


async def test_a_viewer_who_does_not_see_permission_rows_gets_the_same_numbers(
    harness: Harness,
):
    """The owner's timeline has permission rows; another reader's does not.
    The numbers are the version's, so both say v2 for the same version."""
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    await hub.set_permission(entry, Permission(visibility="public"))
    await _publish(hub, "two")

    events = harness.client.get(f"/skill-hub/entries/{entry}/history").json()["events"]

    assert [(e["kind"], e["version"]) for e in events] == [("publish", 2), ("publish", 1)]


# ── files (D12) ──────────────────────────────────────────────────────────────


async def test_the_detail_and_a_version_list_each_file_with_its_size_and_count_scripts(
    harness: Harness,
):
    hub = _hub(harness)
    entry = await _publish(
        hub,
        "one",
        extra={
            "references/g.md": b"12345",
            "scripts/run.py": b"print()\n",
            "scripts/lib/util.sh": b"echo\n",
            "references/scripts/x.md": b"not a script: not the top-level folder",
            "assets/chart.png": b"\x89PNG" + b"x" * 1000,
        },
    )

    detail = harness.client.get(f"/skill-hub/entries/{entry}").json()
    version = harness.client.get(
        f"/skill-hub/entries/{entry}/versions/{_revision(harness, entry)}"
    ).json()

    for body in (detail, version):
        sizes = {f["path"]: f["size"] for f in body["files"]}
        assert sizes["references/g.md"] == 5
        assert sizes["assets/chart.png"] == 1004, "an LFS file's content, not its pointer"
        assert sizes["SKILL.md"] == len(_md("one"))
        assert [f["path"] for f in body["files"]] == sorted(sizes)
        assert body["scripts"] == 2, "files under scripts/ — the skill convention"


# ── where the viewer has it (D3) ─────────────────────────────────────────────


async def test_installs_lists_the_viewers_editable_workspaces_holding_a_copy_of_this_entry(
    harness: Harness,
):
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    other = await _publish(hub, "x", owner="carol")  # same name, another entry
    installed = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry})
    assert installed.status_code == 200, installed.text
    read_only = _item(harness, "theirs", "read_content")
    await _copy(harness, read_only, "triage", entry)
    holds_other = _item(harness, "other copy", "read_content", "edit_content")
    await _copy(harness, holds_other, "triage", other)
    renamed = _item(harness, "renamed", "read_content", "edit_content")
    await _copy(harness, renamed, "my-triage", entry)

    res = harness.client.get(f"/skill-hub/entries/{entry}/installs")

    assert res.status_code == 200, res.text
    assert res.json()["installs"] == [{"app": "rca", "item_id": harness.iid, "title": "t"}]


async def test_installs_of_an_entry_the_viewer_cannot_read_is_404(harness: Harness):
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    await hub.set_permission(entry, Permission(visibility="private"))

    assert harness.client.get(f"/skill-hub/entries/{entry}/installs").status_code == 404


# ── what installing would do (D6) ────────────────────────────────────────────


async def test_targets_lists_an_apps_editable_workspaces_with_what_installing_would_do(
    harness: Harness,
):
    hub = _hub(harness)
    entry = await _publish(hub, "one", tools=["exec", "query_entity"])
    carols = await _publish(hub, "x", owner="carol")
    harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry})
    free = _item(harness, "free", "read_content", "edit_content")
    taken = _item(harness, "taken", "read_content", "edit_content")
    await _copy(harness, taken, "triage", carols)
    hand_written = _item(harness, "hand", "read_content", "edit_content")
    await harness.filestore.write(hand_written, "/.skill/triage/SKILL.md", _md("mine"))
    read_only = _item(harness, "read only", "read_content")

    res = harness.client.get(f"/skill-hub/entries/{entry}/targets", params={"app": "rca"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["missing_tools"] == ["query_entity"]
    by_id = {t["item_id"]: t for t in body["items"]}
    assert read_only not in by_id, "only workspaces the viewer may edit"
    assert by_id[harness.iid]["state"] == "installed"
    assert by_id[free]["state"] == "ok"
    assert (by_id[taken]["state"], by_id[taken]["owner"]) == ("name_taken", "carol")
    assert (by_id[hand_written]["state"], by_id[hand_written]["owner"]) == ("name_taken", "")
    assert by_id[free]["title"] == "free"


async def test_targets_for_an_unknown_app_is_an_empty_list(harness: Harness):
    hub = _hub(harness)
    entry = await _publish(hub, "one")

    res = harness.client.get(f"/skill-hub/entries/{entry}/targets", params={"app": "nope"})

    assert res.status_code == 200, res.text
    assert res.json() == {"missing_tools": [], "items": []}
