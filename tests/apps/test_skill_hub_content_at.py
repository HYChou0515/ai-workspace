"""When a skill hub entry's CONTENT last changed (plan-skill-hub-ux-redo D4,
「最近更新」): `content_at` is written by the two writers that change what the
entry ships — publish and rollback — and by nothing else. A permission change
or a transfer is a new revision too, so the revision's own timestamp is not
this answer; that is why it is a field.
"""

from __future__ import annotations

import datetime as dt
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
from workspace_app.perm import Permission
from workspace_app.resources import make_spec

T0 = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC)


class Clock:
    def __init__(self) -> None:
        self.at = T0

    def __call__(self) -> dt.datetime:
        return self.at

    def tick(self) -> dt.datetime:
        self.at += dt.timedelta(hours=1)
        return self.at


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="alice")
    register_skill_hub(s)
    return s


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(spec: SpecStar, tmp_path: Path, clock: Clock) -> SkillHubStore:
    return SkillHubStore(spec, SkillHubRepos(tmp_path / "git"), now=clock)


async def _publish(store: SkillHubStore, body: bytes) -> str:
    return await store.publish(
        owner="alice",
        name="triage",
        description="d",
        source_item="i",
        source_app="rca",
        source_profile="p",
        payload={"SKILL.md": b"---\nname: triage\ndescription: d\n---\n" + body},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )


def _row(spec: SpecStar, entry_id: str) -> SkillHubEntry:
    data = spec.get_resource_manager(SkillHubEntry).get(entry_id).data
    assert isinstance(data, SkillHubEntry)
    return data


def _revision(spec: SpecStar, entry_id: str) -> str:
    return spec.get_resource_manager(SkillHubEntry).get(entry_id).info.revision_id


async def test_publishing_stamps_content_at_first_and_again_on_a_new_version(
    spec: SpecStar, store: SkillHubStore, clock: Clock
) -> None:
    entry = await _publish(store, b"v1\n")
    assert _row(spec, entry).content_at == T0

    later = clock.tick()
    await _publish(store, b"v2\n")
    assert _row(spec, entry).content_at == later


async def test_a_rollback_stamps_content_at_and_permission_or_transfer_do_not(
    spec: SpecStar, store: SkillHubStore, clock: Clock
) -> None:
    entry = await _publish(store, b"v1\n")
    first = _revision(spec, entry)
    clock.tick()
    await _publish(store, b"v2\n")
    published = _row(spec, entry).content_at

    clock.tick()
    await store.set_permission(entry, Permission(visibility="private"))
    await store.transfer(entry, "bob")
    assert _row(spec, entry).content_at == published

    rolled = clock.tick()
    await store.rollback(entry, first, expected=_row(spec, entry).commit)
    assert _row(spec, entry).content_at == rolled


def test_a_row_written_before_the_field_reads_as_unknown(spec: SpecStar) -> None:
    """No backfill (plan 機制): an old row is `None` until its next publish."""
    rm = spec.get_resource_manager(SkillHubEntry)
    info = rm.create(
        SkillHubEntry(
            owner="alice",
            name="old",
            description="d",
            source_item="i",
            source_app="rca",
            source_profile="p",
            review=SkillHubReview(verdict="ok"),
        )
    )
    assert _row(spec, info.resource_id).content_at is None
