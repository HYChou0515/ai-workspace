"""MigratingFileStore — the #492 M2 dual-read migration wrapper.

During the cut-over from the specstar-blob store (legacy) to the NFS tree
(primary): writes go to primary; reads try primary then FALL BACK to legacy and
lazily backfill; listings UNION both so a not-yet-migrated workspace never
looks empty (the whole point — no phantom data loss mid-migration). Deletes hit
BOTH so a removed file can't be resurrected by the union. `backfill_workspace`
fully migrates one workspace and is the sweeper's unit of work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.migrating import MigratingFileStore
from workspace_app.filestore.nfs_tree import NfsTreeFileStore
from workspace_app.filestore.protocol import FileExists, FileNotFound


@pytest.fixture
def legacy() -> MemoryFileStore:
    return MemoryFileStore()


@pytest.fixture
def primary(tmp_path: Path) -> NfsTreeFileStore:
    return NfsTreeFileStore(tmp_path / "nfs")


@pytest.fixture
def fs(primary: NfsTreeFileStore, legacy: MemoryFileStore) -> MigratingFileStore:
    return MigratingFileStore(primary, legacy)


# ── reads fall back to legacy + lazily backfill ─────────────────────────────


async def test_read_hits_primary_when_present(fs: MigratingFileStore, primary: NfsTreeFileStore):
    await primary.write("ws", "/a", b"new")
    assert await fs.read("ws", "/a") == b"new"


async def test_read_falls_back_to_legacy_and_backfills(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/a", b"old")
    assert await fs.read("ws", "/a") == b"old"
    # lazily copied into primary so the next read (and rsync) sees it there
    assert await primary.read("ws", "/a") == b"old"


async def test_read_truly_missing_raises(fs: MigratingFileStore):
    with pytest.raises(FileNotFound):
        await fs.read("ws", "/nope")


async def test_primary_shadows_a_stale_legacy_copy(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/a", b"stale")
    await primary.write("ws", "/a", b"fresh")
    assert await fs.read("ws", "/a") == b"fresh"


async def test_read_to_file_falls_back_and_backfills(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore, tmp_path: Path
):
    await legacy.write("ws", "/a", b"old")
    dest = tmp_path / "out"
    await fs.read_to_file("ws", "/a", dest)
    assert dest.read_bytes() == b"old"
    assert await primary.exists("ws", "/a")  # backfilled


async def test_read_to_file_primary_hit(
    fs: MigratingFileStore, primary: NfsTreeFileStore, tmp_path: Path
):
    await primary.write("ws", "/a", b"new")
    dest = tmp_path / "out"
    await fs.read_to_file("ws", "/a", dest)
    assert dest.read_bytes() == b"new"


async def test_read_to_file_missing_raises(fs: MigratingFileStore, tmp_path: Path):
    with pytest.raises(FileNotFound):
        await fs.read_to_file("ws", "/nope", tmp_path / "out")


# ── writes go to primary only ───────────────────────────────────────────────


async def test_write_goes_to_primary(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await fs.write("ws", "/a", b"x")
    assert await primary.read("ws", "/a") == b"x"
    assert not await legacy.exists("ws", "/a")


async def test_write_from_path_goes_to_primary(
    fs: MigratingFileStore, primary: NfsTreeFileStore, tmp_path: Path
):
    src = tmp_path / "src"
    src.write_bytes(b"payload")
    await fs.write_from_path("ws", "/a", src, None)
    assert await primary.read("ws", "/a") == b"payload"


async def test_create_exclusive_rejects_when_only_in_legacy(
    fs: MigratingFileStore, legacy: MemoryFileStore
):
    """A path that lives ONLY in legacy must still block an exclusive create —
    else the #419 numbering arbiter would hand out a taken id."""
    await legacy.write("ws", "/issues/1.md", b"old")
    with pytest.raises(FileExists):
        await fs.create_exclusive("ws", "/issues/1.md", b"new")


async def test_create_exclusive_creates_in_primary(
    fs: MigratingFileStore, primary: NfsTreeFileStore
):
    await fs.create_exclusive("ws", "/issues/2.md", b"two")
    assert await primary.read("ws", "/issues/2.md") == b"two"
    with pytest.raises(FileExists):
        await fs.create_exclusive("ws", "/issues/2.md", b"dup")


# ── listings UNION both stores (no phantom emptiness mid-migration) ──────────


async def test_ls_unions_primary_and_legacy(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/only-legacy", b"a")
    await primary.write("ws", "/only-primary", b"b")
    await legacy.write("ws", "/both", b"old")
    await primary.write("ws", "/both", b"new")
    assert sorted(await fs.ls("ws")) == ["/both", "/only-legacy", "/only-primary"]


async def test_ls_prefix(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/src/a", b"a")
    await primary.write("ws", "/src/b", b"b")
    await primary.write("ws", "/other", b"c")
    assert sorted(await fs.ls("ws", "/src/")) == ["/src/a", "/src/b"]


async def test_exists_is_union(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/l", b"a")
    await primary.write("ws", "/p", b"b")
    assert await fs.exists("ws", "/l")
    assert await fs.exists("ws", "/p")
    assert not await fs.exists("ws", "/missing")


async def test_is_dir_is_union(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/ld/a", b"a")
    await primary.write("ws", "/pd/a", b"b")
    assert await fs.is_dir("ws", "/ld")
    assert await fs.is_dir("ws", "/pd")
    assert not await fs.is_dir("ws", "/nope")


async def test_listdir_is_union(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/ld/a", b"a")
    await primary.write("ws", "/pd/a", b"b")
    assert sorted(await fs.listdir("ws")) == ["/ld", "/pd"]


async def test_stat_all_union_primary_wins_on_collision(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/both", b"aaaaa")  # 5
    await primary.write("ws", "/both", b"bb")  # 2 — primary wins
    await legacy.write("ws", "/lonly", b"c")  # 1
    assert sorted(await fs.stat_all("ws")) == [("/both", 2), ("/lonly", 1)]


async def test_workspace_usage_union_primary_wins(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/both", b"aaaaa")  # 5
    await primary.write("ws", "/both", b"bb")  # 2
    await legacy.write("ws", "/lonly", b"c")  # 1
    assert await fs.workspace_usage("ws") == 3  # 2 (primary /both) + 1 (/lonly)


async def test_file_size_prefers_primary_then_legacy(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/l", b"aaaa")  # 4
    await primary.write("ws", "/p", b"bb")  # 2
    assert await fs.file_size("ws", "/p") == 2
    assert await fs.file_size("ws", "/l") == 4
    assert await fs.file_size("ws", "/missing") is None


async def test_census_delegates_to_primary(fs: MigratingFileStore, primary: NfsTreeFileStore):
    await primary.write("ws", "/a", b"a")
    assert (await fs.census())["total_workspacefile_rows"] == 1


# ── deletes hit BOTH so union can't resurrect ───────────────────────────────


async def test_delete_removes_from_both(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/a", b"old")
    await primary.write("ws", "/a", b"new")
    await fs.delete("ws", "/a")
    assert not await fs.exists("ws", "/a")  # gone from the union, not resurrected


async def test_delete_when_only_in_legacy(fs: MigratingFileStore, legacy: MemoryFileStore):
    await legacy.write("ws", "/a", b"old")
    await fs.delete("ws", "/a")
    assert not await fs.exists("ws", "/a")


async def test_delete_missing_raises(fs: MigratingFileStore):
    with pytest.raises(FileNotFound):
        await fs.delete("ws", "/nope")


async def test_mkdir_and_rmdir(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await fs.mkdir("ws", "/d")
    assert await fs.is_dir("ws", "/d")
    # a subtree spanning both stores is fully removed
    await legacy.write("ws", "/d/l", b"a")
    await primary.write("ws", "/d/p", b"b")
    await fs.rmdir("ws", "/d")
    assert not await fs.is_dir("ws", "/d")
    assert await fs.ls("ws") == []


async def test_mkdir_over_a_legacy_file_raises(fs: MigratingFileStore, legacy: MemoryFileStore):
    await legacy.write("ws", "/a", b"x")
    with pytest.raises(FileExists):
        await fs.mkdir("ws", "/a")


async def test_rmdir_missing_raises(fs: MigratingFileStore):
    with pytest.raises(FileNotFound):
        await fs.rmdir("ws", "/nope")


# ── backfill (the sweeper's unit of work) ───────────────────────────────────


async def test_backfill_workspace_copies_all_legacy_files(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/a", b"a")
    await legacy.write("ws", "/d/b", b"b")
    await legacy.mkdir("ws", "/empty")
    n = await fs.backfill_workspace("ws")
    assert n == 2  # two files copied
    assert await primary.read("ws", "/a") == b"a"
    assert await primary.read("ws", "/d/b") == b"b"
    assert await primary.is_dir("ws", "/empty")  # empty dirs migrate too


async def test_backfill_is_idempotent_and_skips_already_present(
    fs: MigratingFileStore, primary: NfsTreeFileStore, legacy: MemoryFileStore
):
    await legacy.write("ws", "/a", b"legacy")
    await primary.write("ws", "/a", b"already-newer")
    n = await fs.backfill_workspace("ws")
    assert n == 0  # already in primary → not overwritten
    assert await primary.read("ws", "/a") == b"already-newer"
