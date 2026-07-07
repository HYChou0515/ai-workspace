"""NfsTreeFileStore — a FileStore backed by a real on-disk directory tree
(the #492 NFS archive). The contract mirrors MemoryFileStore / SpecstarFileStore
so callers swap it in; these tests pin the shared contract PLUS the
filesystem-specific concerns: path-traversal safety, atomic writes, empty dirs
that live natively on disk, and `du`-based usage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.filestore.nfs_tree import NfsTreeFileStore
from workspace_app.filestore.protocol import FileExists, FileNotFound


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "nfs"


@pytest.fixture
def fs(root: Path) -> NfsTreeFileStore:
    return NfsTreeFileStore(root)


# ── shared contract (mirrors test_memory.py) ────────────────────────────────


async def test_write_read_roundtrip(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a.txt", b"hello")
    assert await fs.read("ws-1", "/a.txt") == b"hello"


async def test_write_creates_ancestor_dirs(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/deep/nested/a.txt", b"x")
    assert await fs.is_dir("ws-1", "/deep")
    assert await fs.is_dir("ws-1", "/deep/nested")


async def test_write_overwrites(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a.txt", b"one")
    await fs.write("ws-1", "/a.txt", b"two")
    assert await fs.read("ws-1", "/a.txt") == b"two"


async def test_read_missing_raises(fs: NfsTreeFileStore):
    with pytest.raises(FileNotFound):
        await fs.read("ws-1", "/nope")


async def test_read_of_a_directory_raises(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/d/a.txt", b"x")
    with pytest.raises(FileNotFound):
        await fs.read("ws-1", "/d")


async def test_create_exclusive_creates_then_rejects_duplicate(fs: NfsTreeFileStore):
    await fs.create_exclusive("ws-1", "/issues/1.md", b"one")
    assert await fs.read("ws-1", "/issues/1.md") == b"one"
    with pytest.raises(FileExists):
        await fs.create_exclusive("ws-1", "/issues/1.md", b"two")
    assert await fs.read("ws-1", "/issues/1.md") == b"one"


async def test_ls_filters_by_prefix(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/src/a.py", b"a")
    await fs.write("ws-1", "/src/b.py", b"b")
    await fs.write("ws-1", "/README", b"r")
    assert sorted(await fs.ls("ws-1", "/src/")) == ["/src/a.py", "/src/b.py"]


async def test_ls_empty_prefix_returns_all(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a", b"a")
    await fs.write("ws-1", "/nested/b", b"b")
    assert sorted(await fs.ls("ws-1")) == ["/a", "/nested/b"]


async def test_ls_of_missing_workspace_is_empty(fs: NfsTreeFileStore):
    assert await fs.ls("ghost") == []


async def test_exists(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a", b"a")
    assert await fs.exists("ws-1", "/a")
    assert not await fs.exists("ws-1", "/b")


async def test_exists_of_a_directory_is_false(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/d/a", b"a")
    assert not await fs.exists("ws-1", "/d")  # a dir is not a file


async def test_delete_removes_file_keeps_parent_dir(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/d/a", b"a")
    await fs.delete("ws-1", "/d/a")
    assert not await fs.exists("ws-1", "/d/a")
    assert await fs.is_dir("ws-1", "/d")  # parent dir persists (honest FS)


async def test_delete_missing_raises(fs: NfsTreeFileStore):
    with pytest.raises(FileNotFound):
        await fs.delete("ws-1", "/nope")


async def test_delete_of_a_directory_raises(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/d/a", b"a")
    with pytest.raises(FileNotFound):
        await fs.delete("ws-1", "/d")


async def test_isolation_across_workspaces(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a", b"one")
    await fs.write("ws-2", "/a", b"two")
    assert await fs.read("ws-1", "/a") == b"one"
    assert await fs.read("ws-2", "/a") == b"two"


# ── directories ─────────────────────────────────────────────────────────────


async def test_mkdir_creates_empty_dir(fs: NfsTreeFileStore):
    await fs.mkdir("ws-1", "/empty")
    assert await fs.is_dir("ws-1", "/empty")
    assert await fs.ls("ws-1") == []  # no files


async def test_mkdir_is_idempotent(fs: NfsTreeFileStore):
    await fs.mkdir("ws-1", "/d")
    await fs.mkdir("ws-1", "/d")  # no raise
    assert await fs.is_dir("ws-1", "/d")


async def test_mkdir_over_a_file_raises(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a", b"a")
    with pytest.raises(FileExists):
        await fs.mkdir("ws-1", "/a")


async def test_rmdir_removes_subtree(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/d/a", b"a")
    await fs.write("ws-1", "/d/sub/b", b"b")
    await fs.rmdir("ws-1", "/d")
    assert not await fs.is_dir("ws-1", "/d")
    assert await fs.ls("ws-1") == []


async def test_rmdir_missing_raises(fs: NfsTreeFileStore):
    with pytest.raises(FileNotFound):
        await fs.rmdir("ws-1", "/nope")


async def test_is_dir_false_for_file_and_missing(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a", b"a")
    assert not await fs.is_dir("ws-1", "/a")
    assert not await fs.is_dir("ws-1", "/missing")


async def test_listdir_returns_dirs_under_prefix(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/src/a.py", b"a")
    await fs.write("ws-1", "/src/sub/b.py", b"b")
    await fs.mkdir("ws-1", "/empty")
    assert sorted(await fs.listdir("ws-1")) == ["/empty", "/src", "/src/sub"]
    assert sorted(await fs.listdir("ws-1", "/src/")) == ["/src/sub"]


async def test_listdir_of_missing_workspace_is_empty(fs: NfsTreeFileStore):
    assert await fs.listdir("ghost") == []


# ── streaming variants (#219) ───────────────────────────────────────────────


async def test_write_from_path_and_read_to_file(fs: NfsTreeFileStore, tmp_path: Path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    await fs.write_from_path("ws-1", "/big/x.bin", src, None)
    assert await fs.read("ws-1", "/big/x.bin") == b"payload"
    dest = tmp_path / "out.bin"
    await fs.read_to_file("ws-1", "/big/x.bin", dest)
    assert dest.read_bytes() == b"payload"


async def test_read_to_file_missing_raises(fs: NfsTreeFileStore, tmp_path: Path):
    with pytest.raises(FileNotFound):
        await fs.read_to_file("ws-1", "/nope", tmp_path / "out")


# ── usage / stat (#245 / #362 / #407) ───────────────────────────────────────


async def test_workspace_usage_sums_file_sizes(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a", b"aaa")
    await fs.write("ws-1", "/d/b", b"bb")
    assert await fs.workspace_usage("ws-1") == 5


async def test_workspace_usage_of_missing_is_zero(fs: NfsTreeFileStore):
    assert await fs.workspace_usage("ghost") == 0


async def test_file_size(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a", b"abcd")
    assert await fs.file_size("ws-1", "/a") == 4
    assert await fs.file_size("ws-1", "/missing") is None


async def test_stat_all(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a", b"a")
    await fs.write("ws-1", "/d/b", b"bb")
    assert sorted(await fs.stat_all("ws-1")) == [("/a", 1), ("/d/b", 2)]
    assert await fs.stat_all("ws-1", "/d/") == [("/d/b", 2)]


async def test_census(fs: NfsTreeFileStore):
    await fs.write("ws-1", "/a", b"a")
    await fs.write("ws-1", "/b", b"b")
    await fs.write("ws-2", "/c", b"c")
    assert await fs.census() == {
        "total_workspacefile_rows": 3,
        "n_workspaces": 2,
        "max_files_per_ws": 2,
    }


async def test_census_empty(fs: NfsTreeFileStore):
    assert await fs.census() == {
        "total_workspacefile_rows": 0,
        "n_workspaces": 0,
        "max_files_per_ws": 0,
    }


# ── atomic write ─────────────────────────────────────────────────────────────


async def test_write_is_atomic_no_partial_temp_left(fs: NfsTreeFileStore, root: Path):
    await fs.write("ws-1", "/a", b"final")
    # No stray temp files left beside the target after a successful write.
    item_root = root / "ws-1"
    stray = [p.name for p in item_root.rglob("*") if p.is_file() and p.name != "a"]
    assert stray == []


# ── path-traversal safety ───────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["/../escape", "/a/../../escape", "/a/../../../etc/x"])
async def test_write_rejects_traversal_escaping_item_dir(fs: NfsTreeFileStore, bad: str):
    with pytest.raises(ValueError):
        await fs.write("ws-1", bad, b"x")


async def test_inner_dotdot_that_stays_inside_is_allowed(fs: NfsTreeFileStore):
    # /a/../b resolves to /b — still inside the item dir, so it's fine.
    await fs.write("ws-1", "/a/../b", b"x")
    assert await fs.read("ws-1", "/b") == b"x"


@pytest.mark.parametrize("bad_ws", ["../evil", "a/b", "..", "/abs"])
async def test_rejects_unsafe_workspace_id(fs: NfsTreeFileStore, bad_ws: str):
    with pytest.raises(ValueError):
        await fs.write(bad_ws, "/a", b"x")


async def test_read_rejects_traversal(fs: NfsTreeFileStore):
    with pytest.raises(ValueError):
        await fs.read("ws-1", "/../../etc/passwd")


# ── defensive paths (temp cleanup, empty listings, census skips) ─────────────


async def test_write_cleans_up_temp_on_failure(
    fs: NfsTreeFileStore, root: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the atomic rename fails, the sibling temp must not be left behind."""
    monkeypatch.setattr("workspace_app.filestore.nfs_tree.os.replace", _boom)
    with pytest.raises(RuntimeError):
        await fs.write("ws-1", "/a", b"x")
    assert list((root / "ws-1").glob(".wstmp-*")) == []


async def test_write_from_path_cleans_up_temp_on_failure(
    fs: NfsTreeFileStore, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    src = tmp_path / "src"
    src.write_bytes(b"x")
    monkeypatch.setattr("workspace_app.filestore.nfs_tree.os.replace", _boom)
    with pytest.raises(RuntimeError):
        await fs.write_from_path("ws-1", "/a", src, None)
    assert list((root / "ws-1").glob(".wstmp-*")) == []


async def test_stat_all_of_missing_workspace_is_empty(fs: NfsTreeFileStore):
    assert await fs.stat_all("ghost") == []


async def test_census_skips_stray_files_and_empty_item_dirs(fs: NfsTreeFileStore, root: Path):
    await fs.write("ws-1", "/a", b"a")
    root.mkdir(parents=True, exist_ok=True)
    (root / "stray.txt").write_bytes(b"not an item dir")  # a file under root, skipped
    (root / "ws-empty").mkdir()  # an item dir with no files, not counted
    assert await fs.census() == {
        "total_workspacefile_rows": 1,
        "n_workspaces": 1,
        "max_files_per_ws": 1,
    }


def _boom(*_a: object, **_k: object) -> None:
    raise RuntimeError("boom")
