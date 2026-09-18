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
