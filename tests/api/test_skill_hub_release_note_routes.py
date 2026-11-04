"""Release notes over the skill hub routes (docs/plan-skill-hub-release-notes.md)."""

from __future__ import annotations

from workspace_app.apps.skill_hub import SkillHubReview, SkillHubStore
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
