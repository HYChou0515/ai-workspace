"""Release notes over the skill hub routes (docs/plan-skill-hub-release-notes.md)."""

from __future__ import annotations

from workspace_app.apps.skill_hub import SkillHubEntry, SkillHubReview, SkillHubStore
from workspace_app.perm import Permission

from .conftest import Harness


def _md(body: str) -> bytes:
    return f"---\nname: triage\ndescription: d\n---\n{body}\n".encode()


def _hub(harness: Harness) -> SkillHubStore:
    return harness.spa_client.app.state.skill_hub  # ty: ignore[unresolved-attribute]


async def _publish(hub: SkillHubStore, body: str, note: str = "") -> str:
    return await hub.publish(
        owner="alice",
        name="triage",
        description=body,
        source_item="inv-src",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md(body)},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
        release_note=note,
    )


async def test_the_timeline_carries_each_versions_release_note(harness: Harness):
    hub = _hub(harness)
    entry = await _publish(hub, "one")
    await _publish(hub, "two", "Clearer steps.")
    await hub.set_permission(entry, Permission(visibility="public"))

    events = harness.client.get(f"/skill-hub/entries/{entry}/history").json()["events"]
    assert [(e["version"], e["release_note"]) for e in events] == [(2, "Clearer steps."), (1, "")]


def _row(harness: Harness) -> dict:
    return next(
        s
        for s in harness.client.get(harness.wpath("/skills")).json()["skills"]
        if s["name"] == "triage"
    )


async def _public(hub: SkillHubStore, body: str, note: str = "") -> str:
    entry = await _publish(hub, body, note)
    await hub.set_permission(entry, Permission(visibility="public"))
    return entry


async def test_a_copy_behind_lists_every_version_after_its_own_newest_first(harness: Harness):
    """D5: the reminder shows the note of every version after the installed one."""
    hub = _hub(harness)
    entry = await _public(hub, "one")
    installed = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry})
    assert installed.status_code == 200, installed.text
    assert _row(harness)["updates"] == [], "an up-to-date copy lists no update"

    await _publish(hub, "two", "Adds step two.")
    await _publish(hub, "three", "Adds step three.")

    row = _row(harness)
    assert row["update_available"] is True
    assert [(u["version"], u["kind"], u["release_note"]) for u in row["updates"]] == [
        (3, "publish", "Adds step three."),
        (2, "publish", "Adds step two."),
    ]


async def test_a_rollback_below_the_installed_version_is_the_update(harness: Harness):
    """D5: the author took the skill back below what this item has."""
    hub = _hub(harness)
    entry = await _public(hub, "one")
    first = harness.spec.get_resource_manager(SkillHubEntry).get(entry).info.revision_id
    await _publish(hub, "two", "Adds step two.")
    installed = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry})
    assert installed.status_code == 200, installed.text
    row = hub.get(entry)
    assert row is not None
    await hub.rollback(entry, first, expected=row.commit)

    (update,) = _row(harness)["updates"]
    assert (update["version"], update["kind"], update["back_to"]) == (3, "rollback", 1)
