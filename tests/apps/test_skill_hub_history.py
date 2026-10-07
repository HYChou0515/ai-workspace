"""A skill hub entry's history (docs/plan-skill-hub-history.md §8, G24):
one timeline row per revision that changed something a person would ask
about, any version readable, any two comparable.
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
    register_skill_hub,
)
from workspace_app.apps.skill_hub_git import SkillHubRepos
from workspace_app.perm import Permission
from workspace_app.resources import make_spec

_PNG = b"\x89PNG" + bytes(range(256)) * 8
V1 = {
    "SKILL.md": b"---\nname: triage\ndescription: one\n---\nline\n",
    "a.md": b"a\n",
    "x.png": _PNG,
}
V2 = {
    "SKILL.md": b"---\nname: triage\ndescription: two\n---\nline\nmore\n",
    "b.md": b"b\n",
    "x.png": _PNG + b"!",
}


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="alice")
    register_skill_hub(s)
    return s


@pytest.fixture
def store(spec: SpecStar, tmp_path: Path) -> SkillHubStore:
    return SkillHubStore(spec, SkillHubRepos(tmp_path / "git"))


async def _publish(store: SkillHubStore, payload: dict[str, bytes], description: str) -> str:
    return await store.publish(
        owner="alice",
        name="triage",
        description=description,
        source_item="i",
        source_app="rca",
        source_profile="p",
        payload=payload,
        referenced_tools=[],
        review=SkillHubReview(verdict="ok", notes=[f"note {description}"]),
    )


def _revision(spec: SpecStar, entry_id: str) -> str:
    return spec.get_resource_manager(SkillHubEntry).get(entry_id).info.revision_id


async def test_the_timeline_names_what_each_revision_did_newest_first(
    spec: SpecStar, store: SkillHubStore
) -> None:
    entry = await _publish(store, V1, "one")
    first = _revision(spec, entry)
    await _publish(store, V2, "two")
    second = _revision(spec, entry)
    await store.set_permission(entry, Permission(visibility="private"))
    row = store.get(entry)
    assert row is not None
    await store.rollback(entry, first, expected=row.commit)
    await store.transfer(entry, "bob")

    events = await store.history(entry, viewer="bob")

    assert [e.kind for e in events] == ["transfer", "rollback", "permission", "publish", "publish"]
    newest, rollback, _perm, two, one = events
    assert (newest.by, newest.owner) == ("alice", "bob")
    assert rollback.to_revision == first
    assert (two.revision, two.description, two.review_notes) == (second, "two", ["note two"])
    assert (one.revision, one.by) == (first, "alice")
    assert [e.current for e in events] == [True, False, False, False, False]
    # Every row names the version that was current after it.
    assert one.commit == rollback.commit == newest.commit != two.commit


async def test_who_could_see_it_is_the_owners_alone(spec: SpecStar, store: SkillHubStore) -> None:
    """G24: visibility changes (and the grant lists) are not other people's business."""
    entry = await _publish(store, V1, "one")
    await store.set_permission(entry, Permission(visibility="private"))
    await store.set_permission(entry, Permission(visibility="public"))

    assert [e.kind for e in await store.history(entry, viewer="alice")] == [
        "permission",
        "permission",
        "publish",
    ]
    assert [e.kind for e in await store.history(entry, viewer="carol")] == ["publish"]


async def test_a_revision_that_only_carries_the_draft_is_not_a_row(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """A first publish writes a draft row, then the version — one event."""
    entry = await _publish(store, V1, "one")
    assert len(spec.get_resource_manager(SkillHubEntry).list_revisions(entry)) > 1
    assert [e.kind for e in await store.history(entry, viewer="alice")] == ["publish"]


async def test_any_version_reads_back_as_it_was(spec: SpecStar, store: SkillHubStore) -> None:
    entry = await _publish(store, V1, "one")
    first = _revision(spec, entry)
    await _publish(store, V2, "two")

    old = await store.version(entry, first)

    assert old.description == "one"
    assert await store.repos.read(entry, old.commit) == V1
    with pytest.raises(UnknownRevision):
        await store.version(entry, "nope")


async def test_two_versions_compare_file_by_file(spec: SpecStar, store: SkillHubStore) -> None:
    entry = await _publish(store, V1, "one")
    first = _revision(spec, entry)
    await _publish(store, V2, "two")
    second = _revision(spec, entry)

    diff = await store.diff(entry, first, second)

    by_path = {f.path: f for f in diff}
    assert {p: f.status for p, f in by_path.items()} == {
        "SKILL.md": "changed",
        "a.md": "removed",
        "b.md": "added",
        "x.png": "changed",
    }
    assert "+more" in (by_path["SKILL.md"].patch or "")
    assert "-a" in (by_path["a.md"].patch or "")
    assert by_path["x.png"].patch is None, "an LFS file is compared, never printed"
    assert await store.diff(entry, first, first) == []


async def test_reading_the_history_puts_back_a_tag_a_crash_left_out(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """G8: the row is written before its tag, so a pod that dies between the
    two leaves the current revision untagged; the next read repairs it."""
    import subprocess
    from urllib.parse import quote

    entry = await _publish(store, V1, "one")
    current = _revision(spec, entry)
    subprocess.run(
        [
            "git",
            "-C",
            str(store.repos.path(entry)),
            "update-ref",
            "-d",
            f"refs/tags/r-{quote(current, safe='-._')}",
        ],
        check=True,
    )
    assert current not in await store.repos.tagged_revisions(entry)

    await store.history(entry, viewer="alice")

    row = store.get(entry)
    assert row is not None
    assert (await store.repos.tagged_revisions(entry))[current] == row.commit


async def test_a_visibility_row_says_who_it_was_opened_to(store: SkillHubStore) -> None:
    """Round 2 (conformance L5): G24 — the owner sees visibility changes
    「含名單」; the row carried only the visibility word."""
    entry = await _publish(store, V1, "one")
    await store.set_permission(
        entry,
        Permission(visibility="restricted", read_content=["user:bob", "group:qa"]),
    )

    (perm, _pub) = await store.history(entry, viewer="alice")

    assert (perm.kind, perm.visibility, perm.audience) == (
        "permission",
        "restricted",
        ["user:bob", "group:qa"],
    )


async def test_only_a_restricted_row_names_who_it_was_opened_to(store: SkillHubStore) -> None:
    """Round 3 (veracity #1): unpublish / republish keep the grant lists, and
    those lists only mean anything while the entry is restricted — a private
    row said 「開放給：bob」 to a bob who could not read it."""
    entry = await _publish(store, V1, "one")
    for visibility in ("restricted", "private", "public"):
        await store.set_permission(
            entry,
            Permission(visibility=visibility, read_content=["user:bob"]),
        )

    rows = [(e.visibility, e.audience) for e in await store.history(entry, viewer="alice")]

    assert rows[:3] == [("public", []), ("private", []), ("restricted", ["user:bob"])]


async def test_a_management_write_to_a_deleted_entry_records_nothing(store: SkillHubStore) -> None:
    """Round 3 (veracity #4): the read side of `_change`'s deleted-row case."""
    entry = await _publish(store, V1, "one")
    await store.delete(entry)

    await store.set_permission(entry, Permission(visibility="private"))
    await store.transfer(entry, "bob")

    assert store.get(entry) is None
