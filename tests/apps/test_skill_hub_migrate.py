"""Moving entries published before the git store into it
(docs/plan-skill-hub-history.md §6, G10).

Each such entry gets its own repo whose first commit is the files its `blobs`
namespace holds; the row then names that commit and is tagged. Two entries
with one `owner/name` are reported, never merged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from specstar import SpecStar

from workspace_app.apps.skill_hub import (
    SkillHubEntry,
    SkillHubReview,
    SkillHubStore,
    register_skill_hub,
)
from workspace_app.apps.skill_hub_git import SkillHubRepos
from workspace_app.apps.skill_payload import origin_for
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec

_MD = b"---\nname: triage\ndescription: d\n---\nbody\n"
_PNG = b"\x89PNG" + bytes(range(256)) * 8
PAYLOAD = {"SKILL.md": _MD, "references/a.md": b"a\n", "shot.png": _PNG}


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="system")
    register_skill_hub(s)
    return s


@pytest.fixture
def legacy() -> MemoryFileStore:
    return MemoryFileStore()


@pytest.fixture
def store(spec: SpecStar, legacy: MemoryFileStore, tmp_path: Path) -> SkillHubStore:
    return SkillHubStore(spec, SkillHubRepos(tmp_path / "git"), legacy=legacy)


async def _legacy_row(
    spec: SpecStar,
    legacy: MemoryFileStore,
    entry_id: str,
    *,
    owner: str = "alice",
    name: str = "triage",
    payload: dict[str, bytes] = PAYLOAD,
) -> None:
    ns = f"skill-hub:{entry_id}:v1"
    for rel, data in payload.items():
        await legacy.write(ns, f"/{rel}", data)
    spec.get_resource_manager(SkillHubEntry).create(
        SkillHubEntry(
            owner=owner,
            name=name,
            description="d",
            source_item="i",
            source_app="rca",
            source_profile="p",
            review=SkillHubReview(verdict="ok"),
            blobs=ns,
            origin=origin_for("hub", payload, entry=entry_id),
        ),
        resource_id=entry_id,
    )


async def test_a_legacy_entry_becomes_a_repo_whose_first_version_is_its_files(
    spec: SpecStar, legacy: MemoryFileStore, store: SkillHubStore
) -> None:
    await _legacy_row(spec, legacy, "old1")

    report = await store.migrate_legacy()

    assert report.migrated == ["old1"]
    row = store.get("old1")
    assert row is not None and row.commit
    assert await store.repos.master("old1") == row.commit
    assert await store.repos.read("old1", row.commit) == PAYLOAD
    assert await store.payload_of("old1") == PAYLOAD
    # Its revision is tagged at that version, like any write (§4.4).
    assert set((await store.repos.tagged_revisions("old1")).values()) == {row.commit}


async def test_running_it_again_changes_nothing(
    spec: SpecStar, legacy: MemoryFileStore, store: SkillHubStore
) -> None:
    await _legacy_row(spec, legacy, "old1")
    await store.migrate_legacy()
    row = store.get("old1")
    assert row is not None

    again = await store.migrate_legacy()

    assert again.migrated == []
    after = store.get("old1")
    assert after is not None and after.commit == row.commit


async def test_a_run_cut_short_after_moving_master_adopts_that_commit(
    spec: SpecStar, legacy: MemoryFileStore, store: SkillHubStore
) -> None:
    """The lease is the lock here too: another pod (or a run that died before
    writing the row) already made the repo's first version. The row takes
    THAT commit — a second first version would leave two histories."""
    await _legacy_row(spec, legacy, "old1")
    first = await store.repos.write_version(
        "old1", PAYLOAD, parent=None, author="another-pod", message="the other runner's"
    )
    assert await store.repos.move_master("old1", first, expected=None)

    report = await store.migrate_legacy()

    assert report.migrated == ["old1"]
    row = store.get("old1")
    assert row is not None and row.commit == first


async def test_two_entries_with_one_owner_and_name_are_reported_not_merged(
    spec: SpecStar, legacy: MemoryFileStore, store: SkillHubStore
) -> None:
    await _legacy_row(spec, legacy, "old1")
    await _legacy_row(spec, legacy, "old2", payload={**PAYLOAD, "references/a.md": b"other\n"})
    await _legacy_row(spec, legacy, "old3", owner="bob")

    report = await store.migrate_legacy()

    assert sorted(report.migrated) == ["old1", "old2", "old3"]
    assert report.duplicates == [["old1", "old2"]]
    for entry_id in ("old1", "old2", "old3"):
        assert store.get(entry_id) is not None, "nothing is merged away"
    two = store.get("old2")
    assert two is not None and (await store.payload_of("old2"))["references/a.md"] == b"other\n"


async def test_an_entry_already_in_git_is_left_alone(store: SkillHubStore) -> None:
    entry_id = await store.publish(
        owner="alice",
        name="triage",
        description="d",
        source_item="i",
        source_app="rca",
        source_profile="p",
        payload=PAYLOAD,
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )
    row = store.get(entry_id)
    assert row is not None

    report = await store.migrate_legacy()

    assert report.migrated == []
    after = store.get(entry_id)
    assert after is not None and after.commit == row.commit


async def test_an_old_entrys_own_top_level_gitattributes_is_reported_not_silently_lost(
    spec: SpecStar, legacy: MemoryFileStore, store: SkillHubStore
) -> None:
    """Round 2 (conformance L1): publish refuses it (W16), but a pre-git
    entry may hold one; the migration used to let the platform's replace it
    without a word."""
    await _legacy_row(spec, legacy, "old1", payload={**PAYLOAD, ".gitattributes": b"* text\n"})

    report = await store.migrate_legacy()

    assert report.migrated == ["old1"]
    assert report.gitattributes_dropped == ["old1"]
    assert await store.payload_of("old1") == PAYLOAD


async def test_no_version_is_written_with_a_top_level_gitattributes(store: SkillHubStore) -> None:
    """The rule sits where the commit is made, so no writer can skip it."""
    with pytest.raises(ValueError, match=".gitattributes"):
        await store.repos.write_version(
            "e1", {**PAYLOAD, ".gitattributes": b"x"}, parent=None, author="a", message="m"
        )
