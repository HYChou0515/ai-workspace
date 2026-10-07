"""The skill hub's storage — an entry per published skill, its versions in git
(docs/plan-skill-hub-history.md §3–§5).

Identity is ``(owner, name)`` over a stable resource id: the id is what installed
copies and forks point at, so an owner transfer must not change it, and a
re-publish by the same owner under the same name must be a new revision of the
same row rather than a second row.
"""

from __future__ import annotations

import datetime as dt
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
from workspace_app.apps.skill_payload import SkillOrigin, origin_for
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec

PAYLOAD = {
    "SKILL.md": b"---\nname: triage-reflow\ndescription: Triage reflow defects.\n---\n\n# How\n",
    "references/glossary.md": b"reflow: ...\n",
}
OK = SkillHubReview(verdict="ok", notes=[], model="test")


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="alice")
    register_skill_hub(s)
    return s


@pytest.fixture
def repos(tmp_path: Path) -> SkillHubRepos:
    return SkillHubRepos(tmp_path / "git")


@pytest.fixture
def store(spec: SpecStar, repos: SkillHubRepos) -> SkillHubStore:
    return SkillHubStore(spec, repos)


async def _publish(
    store: SkillHubStore,
    owner: str = "alice",
    *,
    payload: dict[str, bytes] = PAYLOAD,
    forked_from: str = "",
) -> str:
    # Explicit parameters, not a `**dict`: a shared kwargs dict silently costs
    # the type check on every value it carries.
    return await store.publish(
        owner=owner,
        name="triage-reflow",
        description="Triage reflow defects.",
        source_item="rca:i1",
        source_app="rca",
        source_profile="default",
        payload=payload,
        referenced_tools=["exec"],
        review=OK,
        forked_from=forked_from,
    )


async def test_a_published_skill_can_be_read_back_with_its_files(store: SkillHubStore) -> None:
    entry_id = await _publish(store)

    got = store.get(entry_id)
    assert got is not None
    assert got.owner == "alice"
    assert got.name == "triage-reflow"
    assert got.source_item == "rca:i1"
    assert got.referenced_tools == ["exec"]
    assert await store.payload_of(entry_id) == PAYLOAD


async def test_a_copys_manifest_names_the_entry_and_the_commit_and_no_file(
    store: SkillHubStore,
) -> None:
    """G12: a copy records WHICH version it came from, not a hash per file —
    the files' identities are in git, so the manifest stays one line however
    many files the skill has."""
    entry_id = await _publish(store)

    got = store.get(entry_id)
    assert got is not None and len(got.commit) == 40
    manifest = store.copy_manifest(entry_id, got)
    assert (manifest.source, manifest.entry, manifest.commit, manifest.files) == (
        "hub",
        entry_id,
        got.commit,
        {},
    )


async def test_republishing_under_the_same_name_is_a_new_revision_not_a_second_row(
    store: SkillHubStore,
) -> None:
    """`(owner, name)` is the identity. Two rows for one identity would give
    forks and installed copies two ids to point at, and a listing two entries
    to show."""
    first = await _publish(store)
    changed = {**PAYLOAD, "SKILL.md": PAYLOAD["SKILL.md"] + b"\nMore.\n"}
    second = await _publish(store, payload=changed)

    assert second == first, "a re-publish minted a second entry for the same (owner, name)"
    assert await store.payload_of(first) == changed, "the files were not replaced"
    assert store.find("alice", "triage-reflow") == first


async def test_the_same_name_under_a_different_owner_is_a_different_entry(
    store: SkillHubStore,
) -> None:
    """The control for the test above: the identity is the PAIR, not the name.
    bob's `triage-reflow` is bob's, and never overwrites alice's."""
    alices = await _publish(store, owner="alice")
    bobs = await _publish(store, owner="bob", forked_from=alices)

    assert bobs != alices
    alice_row = store.get(alices)
    assert alice_row is not None and alice_row.owner == "alice"
    got = store.get(bobs)
    assert got is not None and got.forked_from == alices


async def test_a_new_entry_is_public_and_a_root(store: SkillHubStore) -> None:
    """Q6: published defaults to public. Q4: an entry with no `forked_from` is a
    root — the thing the listing shows at the top level."""
    entry_id = await _publish(store)
    got = store.get(entry_id)
    assert got is not None
    assert got.permission.visibility == "public"
    assert got.forked_from == ""


def test_an_origin_written_before_the_entry_field_existed_still_decodes() -> None:
    """`.origin` files already sit in workspaces from the shared/profile sources.
    Adding `entry` to `SkillOrigin` must not make those unreadable — that would
    turn every existing materialized skill into "update available" or worse."""
    old = b'{"source":"shared","files":{"SKILL.md":"abc"}}'
    got = msgspec.json.decode(old, type=SkillOrigin)
    assert got.source == "shared"
    assert got.entry == ""


def test_the_entry_struct_is_registered_idempotently(spec: SpecStar) -> None:
    register_skill_hub(spec)
    register_skill_hub(spec)  # must not raise
    assert spec.get_resource_manager(SkillHubEntry) is not None


async def test_a_file_the_new_version_dropped_does_not_survive_the_republish(
    store: SkillHubStore,
) -> None:
    """Replace, not merge. A stale `references/old.md` left behind would be
    installed alongside a SKILL.md that no longer mentions it — and the next
    reviewer would find a reference nobody wrote."""
    first = await _publish(store)
    without_reference = {"SKILL.md": PAYLOAD["SKILL.md"]}
    await _publish(store, payload=without_reference)

    assert await store.payload_of(first) == without_reference, (
        "a file the new version dropped survived from the old one"
    )


async def test_republishing_a_fork_keeps_its_lineage(store: SkillHubStore) -> None:
    """`forked_from` is set once, when the fork is born, and a later re-publish
    of that fork must not blank it — the caller re-publishing does not know
    (or pass) where the fork came from; the row does."""
    original = await _publish(store, owner="alice")
    fork = await _publish(store, owner="bob", forked_from=original)
    await _publish(store, owner="bob", payload={"SKILL.md": PAYLOAD["SKILL.md"] + b"x"})

    got = store.get(fork)
    assert got is not None and got.forked_from == original, "the re-publish lost the lineage"


class _BrokenRepos(SkillHubRepos):
    """A git that fails writing the version — the disk filling up mid-publish."""

    async def write_version(self, *a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise OSError("disk full")


async def test_a_publish_that_dies_writing_the_version_leaves_no_row(
    spec: SpecStar, tmp_path: Path
) -> None:
    """§4.1: the row is made first (as a pending draft) so the name can be
    checked; a publish that fails after that removes its own draft, and a
    pending draft is never visible, findable or installable."""
    store = SkillHubStore(spec, _BrokenRepos(tmp_path / "git"))

    with pytest.raises(OSError):
        await _publish(store)

    assert store.find("alice", "triage-reflow") is None
    assert store.visible("alice") == []
    assert _row_ids(spec) == [], "the draft was left behind"


# ── listing for a viewer (plan P6) ───────────────────────────────────────────


async def _named(store: SkillHubStore, owner: str, name: str, *, description: str = "d") -> str:
    md = f"---\nname: {name}\ndescription: {description}\n---\n\nbody".encode()
    return await store.publish(
        owner=owner,
        name=name,
        description=description,
        source_item="i",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": md},
        referenced_tools=[],
        review=OK,
    )


def _private(spec: SpecStar, entry_id: str) -> None:
    from workspace_app.perm import Permission

    rm = spec.get_resource_manager(SkillHubEntry)
    rm.update(
        entry_id,
        msgspec.structs.replace(rm.get(entry_id).data, permission=Permission(visibility="private")),
    )


async def test_visible_lists_what_the_viewer_may_read_and_nothing_else(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """Public entries for everyone; a private one for its owner only; a deleted
    one for nobody. The listing is what the skill hub page and the search tool
    both read, so the rule lives here once."""
    a = await _named(store, "alice", "a-skill")
    b = await _named(store, "bob", "b-skill")
    hidden = await _named(store, "alice", "hidden")
    _private(spec, hidden)
    gone = await _named(store, "carol", "gone")
    spec.get_resource_manager(SkillHubEntry).delete(gone)

    assert [i for i, _ in store.visible("bob")] == [a, b]
    assert [i for i, _ in store.visible("alice")] == [a, b, hidden]
    assert all(isinstance(e, SkillHubEntry) for _, e in store.visible("bob"))


async def test_visible_is_sorted_by_name_then_owner(store: SkillHubStore) -> None:
    await _named(store, "bob", "zeta")
    await _named(store, "carol", "alpha")
    await _named(store, "alice", "alpha")

    assert [(e.name, e.owner) for _, e in store.visible("x")] == [
        ("alpha", "alice"),
        ("alpha", "carol"),
        ("zeta", "bob"),
    ]


async def test_forks_of_is_an_indexed_lookup_by_forked_from(
    spec: SpecStar, store: SkillHubStore
) -> None:
    root = await _publish(store, "alice")
    fork1 = await _publish(store, "bob", forked_from=root)
    fork2 = await _publish(store, "carol", forked_from=root)
    await _publish(store, "dave")  # another root, no relation

    assert sorted(store.forks_of(root)) == sorted([fork1, fork2])
    assert store.forks_of(fork1) == []

    spec.get_resource_manager(SkillHubEntry).delete(fork2)
    assert store.forks_of(root) == [fork1], "a tombstone is not a fork"


async def test_republishing_after_a_delete_is_a_new_entry_and_the_old_id_stays_dead(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """Soft delete is final for the copies that point at the old id (Q10);
    the owner publishing the same name again starts over, it does not revive."""
    old = await _publish(store)
    spec.get_resource_manager(SkillHubEntry).delete(old)

    new = await _publish(store)

    assert new != old
    assert store.get(old) is None and store.get(new) is not None
    assert [i for i, _ in store.visible("alice")] == [new]


# ── the nesting rule ─────────────────────────────────────────────────────────


async def test_nest_forks_keeps_every_hit_and_nests_one_level(store: SkillHubStore) -> None:
    """A fork of a fork was dropped by the first version — skipped as "not a
    root", attached to nothing. Every id in goes out exactly once."""
    from workspace_app.apps.skill_hub import nest_forks

    root = await _publish(store, "alice")
    fork = await _publish(store, "bob", forked_from=root)
    fork_of_fork = await _publish(store, "carol", forked_from=fork)
    orphan = await _publish(store, "dave", forked_from="gone-root")
    hits = {i: e for i, e in store.visible("x")}

    nested = nest_forks(hits)

    assert nested == [(root, [fork]), (fork_of_fork, []), (orphan, [])]
    shown = [i for r, fs in nested for i in (r, *fs)]
    assert sorted(shown) == sorted(hits), "nothing dropped, nothing doubled"


async def test_nest_forks_keeps_the_hits_order_so_visible_decides_it(store: SkillHubStore) -> None:
    """No sort of its own: `visible` already orders by name then owner, and a
    second sort here would be a rule nothing could observe."""
    from workspace_app.apps.skill_hub import nest_forks

    root = await _publish(store, "zed")
    b = await _publish(store, "bob", forked_from=root)
    a = await _publish(store, "alice", forked_from=root)
    hits = {i: e for i, e in store.visible("x")}

    assert nest_forks(hits) == [(root, [a, b])]


# ── review round 1 (A): a re-publish that dies mid-write ─────────────────────


async def test_a_version_is_a_commit_that_master_and_the_row_both_name(
    store: SkillHubStore, repos: SkillHubRepos
) -> None:
    """G5 / G7: master is the current version, the row records it, and the
    row's revision is tagged at it — the two link each way."""
    entry = await _publish(store)
    row = store.get(entry)
    assert row is not None and row.commit
    assert await repos.master(entry) == row.commit
    assert await repos.read(entry, row.commit) == PAYLOAD
    tags = await repos.tagged_revisions(entry)
    assert list(tags.values()) == [row.commit]


async def test_a_republish_is_a_commit_on_top_of_the_current_one(
    store: SkillHubStore, repos: SkillHubRepos
) -> None:
    entry = await _publish(store)
    first = store.get(entry)
    assert first is not None
    await _publish(store, payload={"SKILL.md": PAYLOAD["SKILL.md"]})
    second = store.get(entry)
    assert second is not None and second.commit != first.commit

    assert await repos.master(entry) == second.commit
    import subprocess

    parent = subprocess.run(
        ["git", "-C", str(repos.path(entry)), "rev-parse", f"{second.commit}^"],
        capture_output=True, check=True, text=True,
    ).stdout.strip()  # fmt: skip
    assert parent == first.commit
    # Every revision is tagged at its commit, the old version included.
    assert sorted((await repos.tagged_revisions(entry)).values()) == sorted(
        [first.commit, second.commit]
    )


class _LosesTheFirstRace(SkillHubRepos):
    """Another pod moves master between this one's read and its move — once."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.raced = False

    async def move_master(self, entry_id: str, commit: str, *, expected: str | None) -> bool:
        if not self.raced and expected is not None:
            self.raced = True
            theirs = await self.write_version(
                entry_id, {"SKILL.md": b"theirs"}, parent=expected, author="bob", message="x"
            )
            assert await super().move_master(entry_id, theirs, expected=expected)
        return await super().move_master(entry_id, commit, expected=expected)


async def test_a_republish_that_loses_the_lease_rereads_master_and_goes_on_top(
    spec: SpecStar, tmp_path: Path
) -> None:
    """G5: losing the push is not an error for a publish — it reads the new
    master and commits on top of it."""
    repos = _LosesTheFirstRace(tmp_path / "git")
    store = SkillHubStore(spec, repos)
    entry = await _publish(store)
    await _publish(store, payload={"SKILL.md": b"mine"})

    row = store.get(entry)
    assert row is not None and repos.raced
    assert await repos.master(entry) == row.commit
    assert await store.payload_of(entry) == {"SKILL.md": b"mine"}


async def test_delete_keeps_the_repo(store: SkillHubStore, repos: SkillHubRepos) -> None:
    """§7 Q2: delete is a soft delete of the row; the version history stays."""
    entry = await _publish(store)
    row = store.get(entry)
    assert row is not None

    await store.delete(entry)

    assert store.get(entry) is None
    assert await repos.master(entry) == row.commit


async def test_a_legacy_row_still_serves_its_files_from_the_old_store(spec: SpecStar) -> None:
    """An entry published before the git store has no `commit`, only the
    namespace its files were written to; it is read from there until it is
    migrated (§6)."""
    legacy = MemoryFileStore()
    await legacy.write("skill-hub:old1:v", "/SKILL.md", PAYLOAD["SKILL.md"])
    rm = spec.get_resource_manager(SkillHubEntry)
    rm.create(
        SkillHubEntry(
            owner="alice",
            name="triage-reflow",
            description="d",
            source_item="i",
            source_app="rca",
            source_profile="p",
            origin=origin_for("hub", {"SKILL.md": PAYLOAD["SKILL.md"]}, entry="old1"),
            review=OK,
            blobs="skill-hub:old1:v",
        ),
        resource_id="old1",
    )
    store = SkillHubStore(spec, SkillHubRepos("/nonexistent"), legacy=legacy)

    assert store.find("alice", "triage-reflow") == "old1"
    assert [i for i, _ in store.visible("alice")] == ["old1"]
    assert await store.payload_of("old1") == {"SKILL.md": PAYLOAD["SKILL.md"]}
    assert await store.skill_md_of("old1") == PAYLOAD["SKILL.md"]


# ── two first publishes of one name (G26, §7 Q3) ─────────────────────────────


def _row_ids(spec: SpecStar) -> list[str]:
    """Every row the table holds, drafts and all — what a crash would leave."""
    rm = spec.get_resource_manager(SkillHubEntry)
    return sorted(
        r.meta.resource_id  # ty: ignore[unresolved-attribute] — `returns=["meta"]` fills it
        for r in rm.list_resources(returns=["meta"])
    )


def _pending(spec: SpecStar, entry_id: str, *, age: dt.timedelta) -> None:
    """A draft someone else's first publish of the same name left — still in
    flight (young) or from a pod that died (old)."""
    rm = spec.get_resource_manager(SkillHubEntry)
    rm.create(
        SkillHubEntry(
            owner="alice",
            name="triage-reflow",
            description="d",
            source_item="i",
            source_app="rca",
            source_profile="p",
            origin=origin_for("hub", {}, entry=entry_id),
            review=OK,
            pending=True,
        ),
        resource_id=entry_id,
        now=dt.datetime.now(dt.UTC) - age,
    )


async def test_a_first_publish_that_meets_another_in_flight_steps_aside_and_retries(
    spec: SpecStar, repos: SkillHubRepos
) -> None:
    """The later one deletes itself, waits a random moment and retries; here
    the other publisher gives up meanwhile, so the retry goes through. Exactly
    one entry remains."""
    rm = spec.get_resource_manager(SkillHubEntry)
    _pending(spec, "theirs", age=dt.timedelta(seconds=1))
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        rm.permanently_delete("theirs")  # the other one stepped aside too

    store = SkillHubStore(spec, repos, sleep=sleep)
    entry = await _publish(store)

    assert len(waits) == 1
    assert [i for i, _ in store.visible("alice")] == [entry]
    assert _row_ids(spec) == [entry]


async def test_a_first_publish_that_keeps_meeting_another_gives_up_with_no_row(
    spec: SpecStar, repos: SkillHubRepos
) -> None:
    _pending(spec, "theirs", age=dt.timedelta(seconds=1))

    async def sleep(_seconds: float) -> None:
        return None

    store = SkillHubStore(spec, repos, sleep=sleep)
    with pytest.raises(ValueError, match="same name"):
        await _publish(store)
    assert _row_ids(spec) == ["theirs"]


async def test_a_draft_older_than_ten_minutes_is_a_dead_publishers_and_is_cleared(
    spec: SpecStar, repos: SkillHubRepos
) -> None:
    """§7 Q3: a pod that died mid-publish left a draft that would otherwise
    make every later publish of the name step aside forever."""
    _pending(spec, "dead", age=dt.timedelta(minutes=11))

    async def sleep(_seconds: float) -> None:
        raise AssertionError("a dead draft is not someone to step aside for")

    store = SkillHubStore(spec, repos, sleep=sleep)
    entry = await _publish(store)

    assert _row_ids(spec) == [entry]


async def test_a_pending_draft_is_invisible_everywhere(
    spec: SpecStar, store: SkillHubStore
) -> None:
    _pending(spec, "theirs", age=dt.timedelta(seconds=1))
    assert store.get("theirs") is None
    assert store.find("alice", "triage-reflow") is None
    assert store.visible("alice") == []


# ── the one search-match rule ────────────────────────────────────────────────


def test_matches_query_is_the_rule_both_the_page_and_the_tool_use() -> None:
    """Review round 1: the page's list and the search tool each carried a
    copy of the match. One function, and the two callers import it — a test
    that they agree is then a test that both call it (see the tool test that
    searches `Reflow` and the route test that searches `REFLOW`)."""
    from workspace_app.apps.skill_hub import matches_query

    e = SkillHubEntry(
        owner="a",
        name="reflow-triage",
        description="Find Solder defects.",
        source_item="i",
        source_app="rca",
        source_profile="p",
        origin=SkillOrigin(source="hub", files={}),
        review=OK,
    )
    assert matches_query(e, "") is True
    assert matches_query(e, "  REFLOW ") is True
    assert matches_query(e, "solder") is True
    assert matches_query(e, "deck") is False


async def test_the_store_refuses_a_name_or_a_size_the_publisher_would_have_been_refused_for(
    store: SkillHubStore,
) -> None:
    """The tool refuses these with a sentence before the review; the store is
    where the row is MADE, so it refuses them too — a future caller that skips
    the validator (a route, a script) cannot mint an entry no loader lists or
    one over the cap."""
    from workspace_app.apps.skill_hub import SKILL_HUB_MAX_BYTES

    with pytest.raises(ValueError, match="a/b"):
        await _named(store, "alice", "a/b")
    assert store.find("alice", "a/b") is None
    with pytest.raises(ValueError, match="MiB"):
        await _publish(store, payload={"SKILL.md": b"x" * (SKILL_HUB_MAX_BYTES + 1)})
    assert store.find("alice", "triage-reflow") is None
    # G11: at most 1000 files, in the same check.
    many = {"SKILL.md": PAYLOAD["SKILL.md"], **{f"references/{n}.md": b"x" for n in range(1000)}}
    with pytest.raises(ValueError, match="1000"):
        await _publish(store, payload=many)
    assert store.find("alice", "triage-reflow") is None
