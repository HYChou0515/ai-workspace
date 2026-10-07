"""The skill hub detail page's history (docs/plan-skill-hub-history.md §8):
the timeline, any version, any two compared, a fork from a version. Read by
anyone who may read the entry — the same 404 as every read route otherwise —
with visibility changes for the owner alone."""

from __future__ import annotations

from workspace_app.apps.skill_hub import SkillHubEntry, SkillHubReview, SkillHubStore
from workspace_app.perm import Permission

from .conftest import Harness

VIEWER = "default-user"


def _md(body: str) -> bytes:
    return f"---\nname: triage\ndescription: d\n---\n{body}\n".encode()


def _hub(harness: Harness) -> SkillHubStore:
    return harness.spa_client.app.state.skill_hub  # ty: ignore[unresolved-attribute]


async def _publish(hub: SkillHubStore, owner: str, body: str) -> str:
    return await hub.publish(
        owner=owner,
        name="triage",
        description=body,
        source_item="inv-src",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md(body), "notes.md": body.encode()},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )


def _revision(harness: Harness, entry_id: str) -> str:
    return harness.spec.get_resource_manager(SkillHubEntry).get(entry_id).info.revision_id


async def test_a_reader_sees_the_timeline_any_version_and_a_comparison(harness: Harness):
    hub = _hub(harness)
    entry = await _publish(hub, "alice", "one")
    first = _revision(harness, entry)
    await _publish(hub, "alice", "two")
    second = _revision(harness, entry)
    await hub.set_permission(entry, Permission(visibility="public"))
    base = f"/skill-hub/entries/{entry}"

    timeline = harness.client.get(f"{base}/history")
    assert timeline.status_code == 200, timeline.text
    events = timeline.json()["events"]
    assert [(e["kind"], e["revision"], e["current"]) for e in events] == [
        ("publish", second, True),
        ("publish", first, False),
    ], "a visibility change is the owner's alone"

    version = harness.client.get(f"{base}/versions/{first}")
    assert version.status_code == 200, version.text
    body = version.json()
    assert (body["revision"], body["description"], body["files"]) == (
        first,
        "one",
        ["SKILL.md", "notes.md"],
    )
    assert body["skill_md"] == _md("one").decode()
    one_file = harness.client.get(f"{base}/versions/{first}/file", params={"path": "notes.md"})
    assert one_file.json() == {"path": "notes.md", "text": "one", "size": 3}

    diff = harness.client.get(f"{base}/diff", params={"from": first, "to": second})
    assert diff.status_code == 200, diff.text
    changes = {f["path"]: f for f in diff.json()["files"]}
    assert set(changes) == {"SKILL.md", "notes.md"}
    assert "+two" in changes["notes.md"]["patch"]

    for bad in (f"{base}/versions/nope", f"{base}/versions/{first}/file?path=missing.md"):
        assert harness.client.get(bad).status_code == 404, bad


async def test_an_entry_the_viewer_may_not_read_has_no_history_either(harness: Harness):
    hub = _hub(harness)
    entry = await _publish(hub, "alice", "one")
    first = _revision(harness, entry)
    await hub.set_permission(entry, Permission(visibility="private"))
    base = f"/skill-hub/entries/{entry}"

    for url in (
        f"{base}/history",
        f"{base}/versions/{first}",
        f"{base}/versions/{first}/file?path=SKILL.md",
        f"{base}/diff?from={first}&to={first}",
    ):
        assert harness.client.get(url).status_code == 404, url


async def test_a_fork_from_a_version_lands_in_the_item_as_that_version(harness: Harness):
    hub = _hub(harness)
    entry = await _publish(hub, "alice", "one")
    first = _revision(harness, entry)
    await _publish(hub, "alice", "two")
    unknown = harness.client.post(
        harness.wpath("/skills/fork"), json={"entry_id": entry, "revision": "nope"}
    )
    assert unknown.status_code == 404, unknown.text

    res = harness.client.post(
        harness.wpath("/skills/fork"), json={"entry_id": entry, "revision": first}
    )

    assert res.status_code == 200, res.text
    assert res.json()["name"] == "triage"
    assert await harness.filestore.read(harness.iid, "/.skill/triage/notes.md") == b"one"
    row = next(
        s
        for s in harness.client.get(harness.wpath("/skills")).json()["skills"]
        if s["name"] == "triage"
    )
    assert (row["upstream"], row["update_available"]) == ("live", False)
    # A fork's starting point is not an install of the entry (W4): the row
    # does not name it, so the chat card does not say 「已安裝」.
    assert row["hub_entry"] == ""
    # A second fork into the same name meets the folder already there.
    again = harness.client.post(
        harness.wpath("/skills/fork"), json={"entry_id": entry, "revision": first}
    )
    assert again.status_code == 409, again.text
