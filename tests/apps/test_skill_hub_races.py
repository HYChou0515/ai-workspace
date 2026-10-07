"""Two writers of one skill hub entry at once (review round 1, defect lens #3).

Every writer used to read the whole row, await git, then write the whole row
back — so a publish and an unpublish, or two publishes, overwrote each other:
the row could name a version master had moved past (after which every
rollback was refused), or a publish could undo an unpublish. Now each write
applies only its own change to the row as it is at write time (a CAS on the
revision), and a version is recorded only while it is still master.

"Another pod" is simulated by a write slipped in just before this writer's
own: the shapes a second process produces, not the timings.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
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
from workspace_app.perm import Permission
from workspace_app.resources import make_spec


def _md(body: str) -> bytes:
    return f"---\nname: triage\ndescription: d\n---\n{body}\n".encode()


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="alice")
    register_skill_hub(s)
    return s


@pytest.fixture
def legacy() -> MemoryFileStore:
    return MemoryFileStore()


@pytest.fixture
def store(spec: SpecStar, tmp_path: Path, legacy: MemoryFileStore) -> SkillHubStore:
    return SkillHubStore(spec, SkillHubRepos(tmp_path / "git"), legacy=legacy)


async def _publish(store: SkillHubStore, body: str) -> str:
    return await store.publish(
        owner="alice",
        name="triage",
        description=body,
        source_item="i",
        source_app="rca",
        source_profile="p",
        payload={"SKILL.md": _md(body)},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )


def _slip_in_before_next_write(spec: SpecStar, entry_id: str, change) -> None:  # noqa: ANN001
    """The next `update` of `entry_id` is preceded by another writer's — the
    row moves under the writer between its read and its write."""
    rm = spec.get_resource_manager(SkillHubEntry)
    real = rm.update
    done = False

    def update(resource_id, data, **kw):  # noqa: ANN001, ANN003, ANN202
        nonlocal done
        if resource_id == entry_id and not done:
            done = True
            now = rm.get(entry_id).data
            real(entry_id, change(now))
        return real(resource_id, data, **kw)

    rm.update = update  # ty: ignore[invalid-assignment]


async def test_an_unpublish_racing_a_publish_keeps_both(
    spec: SpecStar, store: SkillHubStore
) -> None:
    entry = await _publish(store, "one")
    hide = Permission(visibility="private")
    _slip_in_before_next_write(
        spec, entry, lambda row: msgspec.structs.replace(row, permission=hide)
    )

    await _publish(store, "two")

    row = store.get(entry)
    assert row is not None
    assert row.permission.visibility == "private", "the publish undid the unpublish"
    assert row.description == "two" and row.commit == await store.repos.master(entry)


async def test_a_publish_racing_an_unpublish_keeps_both(
    spec: SpecStar, store: SkillHubStore
) -> None:
    entry = await _publish(store, "one")
    newer = await store.repos.write_version(
        entry, {"SKILL.md": _md("two")}, parent=await store.repos.master(entry), author="alice",
        message="two",
    )  # fmt: skip
    before = await store.repos.master(entry)
    assert await store.repos.move_master(entry, newer, expected=before)
    _slip_in_before_next_write(
        spec, entry, lambda row: msgspec.structs.replace(row, commit=newer, description="two")
    )

    await store.set_permission(entry, Permission(visibility="private"))

    row = store.get(entry)
    assert row is not None
    assert (row.permission.visibility, row.commit, row.description) == ("private", newer, "two")


async def test_two_publishes_end_with_the_row_on_master(
    spec: SpecStar, store: SkillHubStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 moves master, is held before recording it; P2 moves master on top
    and records it; then P1 resumes. The row must stay on P2 — and a
    rollback from the page that shows it must work."""
    entry = await _publish(store, "one")
    first = spec.get_resource_manager(SkillHubEntry).get(entry).info.revision_id
    real_move = store.repos.move_master
    held, release = asyncio.Event(), asyncio.Event()

    async def move(entry_id, commit, *, expected):  # noqa: ANN001, ANN202
        moved = await real_move(entry_id, commit, expected=expected)
        if not held.is_set():
            held.set()
            await release.wait()
        return moved

    monkeypatch.setattr(store.repos, "move_master", move)
    p1 = asyncio.create_task(_publish(store, "two"))
    await held.wait()
    await _publish(store, "three")
    release.set()
    await p1

    row = store.get(entry)
    assert row is not None
    assert (row.commit, row.description) == (await store.repos.master(entry), "three")
    await store.rollback(entry, first, expected=row.commit)
    back = store.get(entry)
    assert back is not None and back.description == "one"


async def test_republishing_an_entry_not_yet_moved_into_git_keeps_its_old_version(
    spec: SpecStar, store: SkillHubStore, legacy: MemoryFileStore
) -> None:
    """Review round 1 (conformance #3): a re-publish before the operator ran
    the migration made the new content the repo's FIRST commit, and the
    migration then skipped the entry — the pre-git version was gone."""
    old = {"SKILL.md": _md("legacy")}
    await legacy.write("skill-hub:old1:v1", "/SKILL.md", old["SKILL.md"])
    spec.get_resource_manager(SkillHubEntry).create(
        SkillHubEntry(
            owner="alice",
            name="triage",
            description="legacy",
            source_item="i",
            source_app="rca",
            source_profile="p",
            review=SkillHubReview(verdict="ok"),
            blobs="skill-hub:old1:v1",
            origin=origin_for("hub", old, entry="old1"),
        ),
        resource_id="old1",
    )

    assert await _publish(store, "new") == "old1"

    row = store.get("old1")
    assert row is not None
    parent = await store.repos.parent("old1", row.commit)
    assert parent is not None, "the pre-git version is not the new one's parent"
    assert await store.repos.read("old1", parent) == old
    kinds = [e.kind for e in await store.history("old1", viewer="alice")]
    assert kinds == ["publish", "publish"]


async def test_a_whole_publish_from_another_pod_inside_the_check_window_still_wins(
    spec: SpecStar, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2 (defect #1): pod A checked "my commit is still master",
    then pod B published entirely, then A's CAS landed — on B's row — and the
    row went back behind master for good. The check now comes after the row
    is read, so anything B writes in between fails A's CAS."""
    a = SkillHubStore(spec, SkillHubRepos(tmp_path / "git"))
    b = SkillHubStore(spec, SkillHubRepos(tmp_path / "git"))  # another pod: same repos, same DB
    entry = await _publish(a, "one")
    real_master = a.repos.master
    armed = fired = False

    async def master(entry_id):  # noqa: ANN001, ANN202
        nonlocal fired
        seen = await real_master(entry_id)
        if armed and not fired:
            fired = True
            await _publish(b, "three")  # B, start to finish, after A's read of master
        return seen

    real_move = a.repos.move_master

    async def move(entry_id, commit, *, expected):  # noqa: ANN001, ANN202
        nonlocal armed
        moved = await real_move(entry_id, commit, expected=expected)
        armed = True  # from here on A is recording its version
        return moved

    monkeypatch.setattr(a.repos, "master", master)
    monkeypatch.setattr(a.repos, "move_master", move)
    await _publish(a, "two")

    row = a.get(entry)
    assert row is not None and fired
    assert (row.commit, row.description) == (await real_master(entry), "three")


async def test_a_write_to_an_entry_deleted_meanwhile_records_nothing(
    spec: SpecStar, store: SkillHubStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 2 (defect #3): the delete landed between the read and the CAS,
    and the CAS raised out as a 500."""
    entry = await _publish(store, "one")
    rm = spec.get_resource_manager(SkillHubEntry)
    real_update = rm.update

    def update(resource_id, data, **kw):  # noqa: ANN001, ANN003, ANN202
        rm.delete(entry)  # another pod, between this writer's read and its CAS
        return real_update(resource_id, data, **kw)

    monkeypatch.setattr(rm, "update", update)
    await store.set_permission(entry, Permission(visibility="private"))

    assert store.get(entry) is None


async def test_a_rollback_lease_is_a_commit_never_a_ref(store: SkillHubStore) -> None:
    """Round 2 (defect #2): `expected` came from the request and git read it
    as a rev — `refs/heads/master` always matched, bypassing the lease."""
    from workspace_app.apps.skill_hub import VersionMoved
    from workspace_app.apps.skill_hub_git import GitError

    entry = await _publish(store, "one")
    first = store._rm().get(entry).info.revision_id  # noqa: SLF001
    await _publish(store, "two")

    with pytest.raises((VersionMoved, GitError)):
        await store.rollback(entry, first, expected="refs/heads/master")
    row = store.get(entry)
    assert row is not None and row.description == "two"


async def test_a_transfer_onto_someone_mid_first_publish_is_refused(
    store: SkillHubStore,
) -> None:
    """Round 2 (defect #4): `find` skips drafts, so a transfer to bob landed
    beside bob's in-flight first publish of the same name — two live rows."""
    alices = await _publish(store, "alice's")
    store._rm().create(  # noqa: SLF001 — bob's draft, mid-publish
        SkillHubEntry(
            owner="bob",
            name="triage",
            description="d",
            source_item="i",
            source_app="rca",
            source_profile="p",
            review=SkillHubReview(verdict="ok"),
            pending=True,
        ),
        resource_id="bobsdraft",
    )

    assert store.name_taken("bob", "triage")
    assert not store.name_taken("carol", "triage")
    del alices
