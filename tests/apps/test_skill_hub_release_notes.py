"""Release notes on skill hub versions (docs/plan-skill-hub-release-notes.md).

A note is the commit message of the version it describes, so a rollback row
carries the note of the version it brought back.
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
from workspace_app.resources import make_spec

V1 = {"SKILL.md": b"---\nname: triage\ndescription: one\n---\nline\n"}
V2 = {"SKILL.md": b"---\nname: triage\ndescription: two\n---\nline\nmore\n", "b.md": b"b\n"}
V3 = {"SKILL.md": b"---\nname: triage\ndescription: three\n---\nline\nmore\nagain\n"}


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="alice")
    register_skill_hub(s)
    return s


@pytest.fixture
def store(spec: SpecStar, tmp_path: Path) -> SkillHubStore:
    return SkillHubStore(spec, SkillHubRepos(tmp_path / "git"))


async def _publish(store: SkillHubStore, payload: dict[str, bytes], note: str = "") -> str:
    return await store.publish(
        owner="alice",
        name="triage",
        description="d",
        source_item="i",
        source_app="rca",
        source_profile="p",
        payload=payload,
        referenced_tools=[],
        review=SkillHubReview(verdict="ok", notes=[]),
        release_note=note,
    )


def _revision(spec: SpecStar, entry_id: str) -> str:
    return spec.get_resource_manager(SkillHubEntry).get(entry_id).info.revision_id


async def _rollback(store: SkillHubStore, entry: str, to_revision: str) -> None:
    row = store.get(entry)
    assert row is not None
    await store.rollback(entry, to_revision, expected=row.commit)


async def test_each_version_carries_the_release_note_it_was_published_with(
    spec: SpecStar, store: SkillHubStore
) -> None:
    entry = await _publish(store, V1)
    first = _revision(spec, entry)
    await _publish(store, V2, "Adds b.md.\nThe steps are clearer.")
    second = _revision(spec, entry)
    await _rollback(store, entry, first)
    await _rollback(store, entry, second)

    notes = {
        (e.kind, e.version): e.release_note
        for e in await store.history(entry, viewer="alice")
        if e.version is not None
    }
    assert notes == {
        ("publish", 1): "",
        ("publish", 2): "Adds b.md.\nThe steps are clearer.",
        ("rollback", 3): "",
        ("rollback", 4): "Adds b.md.\nThe steps are clearer.",
    }
