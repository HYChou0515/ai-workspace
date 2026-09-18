"""The skill hub's storage — an entry per published skill, its files as blobs.

Identity is ``(owner, name)`` over a stable resource id: the id is what installed
copies and forks point at, so an owner transfer must not change it, and a
re-publish by the same owner under the same name must be a new revision of the
same row rather than a second row.
"""

from __future__ import annotations

import msgspec
import pytest
from specstar import SpecStar

from workspace_app.apps.skill_hub import (
    SkillHubEntry,
    SkillHubReview,
    SkillHubStore,
    register_skill_hub,
)
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
def store(spec: SpecStar) -> SkillHubStore:
    return SkillHubStore(spec, MemoryFileStore())


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


async def test_the_entrys_origin_is_exactly_what_origin_for_computes(
    store: SkillHubStore,
) -> None:
    """PARITY, with `origin_for` as the oracle.

    An installed copy's `.origin` and the hub entry's `origin` must be the same
    manifest, computed by the same function — `skill_update_available` compares
    the two, and two implementations of "the hash of these files" kept alike by
    hand diverge the moment one is edited. So the entry does not compute a hash;
    it stores the one `origin_for` gives it.
    """
    entry_id = await _publish(store)

    got = store.get(entry_id)
    assert got is not None
    assert got.origin == origin_for("hub", PAYLOAD, entry=entry_id)
    assert got.origin.source == "hub"
    assert got.origin.entry == entry_id, (
        "the manifest must name the entry a copy will point back at"
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


async def test_a_publish_that_dies_writing_files_leaves_no_row(spec: SpecStar) -> None:
    """Files before the row, so a row that exists always describes files that
    exist. The other order leaves a row pointing at nothing — an entry the
    listing shows, `install_skill` accepts, and `materialize_skill` then writes
    zero files for.

    Driven by a blob store that fails mid-write; the assertion is on what the
    row store holds afterwards, which is the only thing a crash leaves behind.
    """

    class _DiesOnSecondWrite(MemoryFileStore):
        def __init__(self) -> None:
            super().__init__()
            self.writes = 0

        async def write(self, workspace_id: str, path: str, data: bytes) -> None:
            self.writes += 1
            if self.writes == 2:
                raise OSError("disk full")
            await super().write(workspace_id, path, data)

    store = SkillHubStore(spec, _DiesOnSecondWrite())

    with pytest.raises(OSError):
        await _publish(store)

    assert store.find("alice", "triage-reflow") is None, (
        "the row was created before its files were, and now points at nothing"
    )


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


class _DiesOnNthWrite(MemoryFileStore):
    """A blob store that raises on its n-th write — the durable store hiccup or
    the disk filling up, mid-publish."""

    def __init__(self, nth: int) -> None:
        super().__init__()
        self.writes = 0
        self.nth = nth

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        self.writes += 1
        if self.writes == self.nth:
            raise OSError("disk gone")
        await super().write(workspace_id, path, data)


async def test_a_republish_that_dies_writing_files_leaves_the_live_row_serving_the_old_files(
    spec: SpecStar,
) -> None:
    """The first version of `publish` PURGED the old files before writing the
    new ones, so a failure in between left a LIVE row whose manifest named two
    files and whose namespace held none: installs wrote a folder holding only
    `.origin`, the page showed no SKILL.md, and `skill_folder_in_the_way` read
    that folder as free. Each version now lives in its own namespace; the row
    moves to the new one only after every file is there, and the old one is
    dropped only after that. A crash leaves orphan blobs, never a row pointing
    at nothing — for the re-publish as well as the first publish."""
    v1 = {"SKILL.md": PAYLOAD["SKILL.md"], "references/glossary.md": b"v1\n"}
    v2 = {"SKILL.md": PAYLOAD["SKILL.md"], "references/glossary.md": b"v2\n"}
    blobs = _DiesOnNthWrite(nth=4)  # v1 = writes 1–2; v2 dies on its 2nd file
    store = SkillHubStore(spec, blobs)
    entry = await _publish(store, payload=v1)

    with pytest.raises(OSError):
        await _publish(store, payload=v2)

    live = store.get(entry)
    assert live is not None and live.origin.files == origin_for("hub", v1, entry=entry).files
    assert await store.payload_of(entry) == v1


async def test_a_republish_that_succeeds_drops_the_previous_versions_files(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """The namespaces are per version, so the old one has to be let go of
    explicitly — or every re-publish would leak a full copy."""
    entry = await _publish(store)
    before = store.get(entry)
    assert before is not None
    old_ns = before.blobs

    await _publish(store, payload={"SKILL.md": PAYLOAD["SKILL.md"]})

    after = store.get(entry)
    assert after is not None and after.blobs != old_ns
    assert await store._blobs.ls(old_ns) == []  # noqa: SLF001 — the namespace is the store's own
    assert await store.payload_of(entry) == {"SKILL.md": PAYLOAD["SKILL.md"]}


async def test_delete_purges_the_version_the_row_points_at(
    spec: SpecStar, store: SkillHubStore
) -> None:
    entry = await _publish(store)
    row = store.get(entry)
    assert row is not None

    await store.delete(entry)

    assert await store._blobs.ls(row.blobs) == []  # noqa: SLF001
