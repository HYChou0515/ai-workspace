"""Rolling a skill hub entry back to an earlier version
(docs/plan-skill-hub-history.md §4.3, G6, G18–G20).

Master moves to the old revision's commit under the lease; the row takes the
old revision's content fields and keeps today's owner and visibility; the new
revision is tagged; the next publish builds on the rolled-back commit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from specstar import SpecStar

from workspace_app.apps.skill_hub import (
    SkillHubEntry,
    SkillHubReview,
    SkillHubStore,
    UnknownRevision,
    VersionMoved,
    register_skill_hub,
)
from workspace_app.apps.skill_hub_git import SkillHubRepos
from workspace_app.perm import Permission
from workspace_app.resources import make_spec

V1 = {"SKILL.md": b"---\nname: triage\ndescription: one\n---\nv1\n", "a.md": b"a1\n"}
V2 = {"SKILL.md": b"---\nname: triage\ndescription: two\n---\nv2\n", "b.md": b"b2\n"}


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="alice")
    register_skill_hub(s)
    return s


@pytest.fixture
def store(spec: SpecStar, tmp_path: Path) -> SkillHubStore:
    return SkillHubStore(spec, SkillHubRepos(tmp_path / "git"))


async def _publish(
    store: SkillHubStore, payload: dict[str, bytes], *, description: str, tools: list[str]
) -> str:
    return await store.publish(
        owner="alice",
        name="triage",
        description=description,
        source_item="i",
        source_app="rca",
        source_profile="p",
        payload=payload,
        referenced_tools=tools,
        review=SkillHubReview(verdict="ok", notes=[description]),
    )


def _revision_now(spec: SpecStar, entry_id: str) -> str:
    return spec.get_resource_manager(SkillHubEntry).get(entry_id).info.revision_id


async def _two_versions(spec: SpecStar, store: SkillHubStore) -> tuple[str, str, str]:
    entry = await _publish(store, V1, description="one", tools=["exec"])
    first = _revision_now(spec, entry)
    await _publish(store, V2, description="two", tools=["exec", "query_entity"])
    return entry, first, _revision_now(spec, entry)


async def test_a_rollback_brings_back_the_files_and_content_fields_of_that_version(
    spec: SpecStar, store: SkillHubStore
) -> None:
    entry, first, _second = await _two_versions(spec, store)
    v1 = spec.get_resource_manager(SkillHubEntry).get_resource_revision(entry, first).data
    assert isinstance(v1, SkillHubEntry)
    now = store.get(entry)
    assert now is not None

    commit = await store.rollback(entry, first, expected=now.commit)

    assert commit == v1.commit
    assert await store.repos.master(entry) == v1.commit
    assert await store.payload_of(entry) == V1
    row = store.get(entry)
    assert row is not None
    assert (row.commit, row.description, row.review, row.referenced_tools) == (
        v1.commit,
        "one",
        v1.review,
        ["exec"],
    )
    # The rollback is a revision of its own, tagged at the version it made current.
    tags = await store.repos.tagged_revisions(entry)
    assert tags[_revision_now(spec, entry)] == v1.commit


async def test_a_rollback_keeps_todays_owner_and_visibility(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """G18: not specstar's `switch`, which would bring the old owner and
    permission back with the old content."""
    entry, first, _second = await _two_versions(spec, store)
    await store.set_permission(entry, Permission(visibility="private"))
    await store.transfer(entry, "bob")
    now = store.get(entry)
    assert now is not None

    await store.rollback(entry, first, expected=now.commit)

    row = store.get(entry)
    assert row is not None
    assert (row.owner, row.permission.visibility) == ("bob", "private")


async def test_the_next_publish_builds_on_the_rolled_back_version(
    spec: SpecStar, store: SkillHubStore
) -> None:
    entry, first, _second = await _two_versions(spec, store)
    now = store.get(entry)
    assert now is not None
    back = await store.rollback(entry, first, expected=now.commit)

    await _publish(store, {**V1, "c.md": b"c\n"}, description="three", tools=[])

    row = store.get(entry)
    assert row is not None
    assert await store.repos.parent(entry, row.commit) == back


async def test_a_version_that_moved_since_the_owner_looked_is_refused(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """G6: no automatic retry — the owner chose against what they saw."""
    entry, first, _second = await _two_versions(spec, store)
    seen = store.get(entry)
    assert seen is not None
    await _publish(store, {**V2, "c.md": b"c\n"}, description="three", tools=[])
    before = store.get(entry)
    assert before is not None

    with pytest.raises(VersionMoved):
        await store.rollback(entry, first, expected=seen.commit)

    assert store.get(entry) == before
    assert await store.repos.master(entry) == before.commit


async def test_a_revision_of_another_entry_or_none_at_all_is_unknown(
    spec: SpecStar, store: SkillHubStore
) -> None:
    entry, _first, _second = await _two_versions(spec, store)
    other = await store.publish(
        owner="carol",
        name="other",
        description="d",
        source_item="i",
        source_app="rca",
        source_profile="p",
        payload=V1,
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )
    now = store.get(entry)
    assert now is not None

    for revision in (_revision_now(spec, other), f"{entry}:999", "nonsense"):
        with pytest.raises(UnknownRevision):
            await store.rollback(entry, revision, expected=now.commit)


async def test_rolling_back_to_the_current_version_changes_nothing(
    spec: SpecStar, store: SkillHubStore
) -> None:
    entry, _first, second = await _two_versions(spec, store)
    now = store.get(entry)
    assert now is not None

    assert await store.rollback(entry, second, expected=now.commit) == now.commit

    assert _revision_now(spec, entry) == second
    assert store.get(entry) == now
