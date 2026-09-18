"""The Skills panel over a copy installed from the skill hub (plan P5).

Through the routes the panel actually calls: `GET .../skills` reports the
copy's upstream STATE beside `update_available`, and `POST .../refresh` pulls
a re-published version. What is being pinned is that a hub copy rides the
existing panel machinery — one `is_copy` row, one Refresh — and that a dead
upstream is a state on the row, never a 500 that takes the whole panel down.
"""

from __future__ import annotations

import msgspec

from workspace_app.apps.skill_hub import SkillHubEntry, SkillHubReview, SkillHubStore
from workspace_app.apps.skills import install_hub_skill
from workspace_app.files import WorkspaceFiles
from workspace_app.perm import Permission

from .conftest import Harness


def _md(body: str) -> bytes:
    return f"---\nname: triage\ndescription: Triage.\n---\n\n{body}".encode()


async def _publish(hub: SkillHubStore, body: str = "v1") -> str:
    return await hub.publish(
        owner="alice",
        name="triage",
        description="Triage.",
        source_item="inv-alice",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md(body), "scripts/x.py": body.encode()},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )


def _row(harness: Harness, name: str = "triage") -> dict:
    res = harness.client.get(harness.wpath("/skills"))
    assert res.status_code == 200, res.text
    return next(s for s in res.json()["skills"] if s["name"] == name)


async def _installed(harness: Harness) -> tuple[SkillHubStore, str]:
    hub: SkillHubStore = harness.spa_client.app.state.skill_hub  # ty: ignore[unresolved-attribute]
    entry = await _publish(hub)
    await install_hub_skill(WorkspaceFiles(harness.filestore), harness.iid, hub, entry)
    return hub, entry


async def test_a_fresh_hub_copy_lists_as_a_live_copy_with_nothing_to_update(harness: Harness):
    await _installed(harness)

    row = _row(harness)

    assert row["source"] == "workspace"
    assert (row["is_copy"], row["upstream"], row["update_available"]) == (True, "live", False)


async def test_a_republished_upstream_shows_an_update_and_refresh_brings_it(harness: Harness):
    hub, entry = await _installed(harness)
    assert await _publish(hub, body="v2") == entry

    assert _row(harness)["update_available"] is True
    res = harness.client.post(harness.wpath("/skills/triage/refresh"), json={"force": False})
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == ["SKILL.md", "scripts/x.py"]
    after = _row(harness)
    assert (after["upstream"], after["update_available"]) == ("live", False)
    assert await harness.filestore.read(harness.iid, "/.skill/triage/scripts/x.py") == b"v2"


async def test_an_unpublished_upstream_is_a_state_on_the_row_not_a_broken_panel(harness: Harness):
    hub, entry = await _installed(harness)
    rm = harness.spec.get_resource_manager(SkillHubEntry)
    rm.update(
        entry,
        msgspec.structs.replace(rm.get(entry).data, permission=Permission(visibility="private")),
    )

    row = _row(harness)

    assert (row["is_copy"], row["upstream"], row["update_available"]) == (
        True,
        "unpublished",
        False,
    )
    res = harness.client.post(harness.wpath("/skills/triage/refresh"), json={"force": False})
    assert res.status_code == 200 and res.json()["updated"] == []


async def test_a_deleted_upstream_is_a_state_on_the_row(harness: Harness):
    hub, entry = await _installed(harness)
    harness.spec.get_resource_manager(SkillHubEntry).delete(entry)

    row = _row(harness)

    assert (row["upstream"], row["update_available"]) == ("deleted", False)


async def test_a_hand_written_skill_has_no_upstream(harness: Harness):
    await harness.filestore.write(
        harness.iid, "/.skill/mine/SKILL.md", _md("x").replace(b"triage", b"mine")
    )

    row = _row(harness, "mine")

    assert (row["is_copy"], row["upstream"]) == (False, None)
