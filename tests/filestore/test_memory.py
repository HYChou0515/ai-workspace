"""MemoryFileStore — in-process, no specstar dependency.

The contract mirrors SpecstarFileStore so callers can swap them in.
This test set is intentionally tight: write / read / ls / exists /
delete, plus dirty-path tracking, plus isolation across workspace_id.
"""

from __future__ import annotations

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.protocol import FileExists, FileNotFound


@pytest.fixture
def fs() -> MemoryFileStore:
    return MemoryFileStore()


async def test_write_read_roundtrip(fs: MemoryFileStore):
    await fs.write("ws-1", "/a.txt", b"hello")
    assert await fs.read("ws-1", "/a.txt") == b"hello"


async def test_read_missing_raises(fs: MemoryFileStore):
    with pytest.raises(FileNotFound):
        await fs.read("ws-1", "/nope")


async def test_create_exclusive_creates_then_rejects_a_duplicate(fs: MemoryFileStore):
    """#419 N1 arbiter: create-if-absent lands the first write and raises
    `FileExists` on a second claim of the same path, leaving the winner intact."""
    await fs.create_exclusive("ws-1", "/issues/1.md", b"one")
    assert await fs.read("ws-1", "/issues/1.md") == b"one"
    with pytest.raises(FileExists):
        await fs.create_exclusive("ws-1", "/issues/1.md", b"two")
    assert await fs.read("ws-1", "/issues/1.md") == b"one"


async def test_ls_filters_by_prefix(fs: MemoryFileStore):
    await fs.write("ws-1", "/src/a.py", b"a")
    await fs.write("ws-1", "/src/b.py", b"b")
    await fs.write("ws-1", "/README", b"r")
    assert sorted(await fs.ls("ws-1", "/src/")) == ["/src/a.py", "/src/b.py"]


async def test_ls_empty_prefix_returns_all(fs: MemoryFileStore):
    await fs.write("ws-1", "/a", b"a")
    await fs.write("ws-1", "/b", b"b")
    assert sorted(await fs.ls("ws-1")) == ["/a", "/b"]


async def test_stat_all_returns_path_and_size(fs: MemoryFileStore):
    # #362: batch (path, size) so the file-tree endpoint needn't read bytes.
    await fs.write("ws-1", "/a.txt", b"hello")
    await fs.write("ws-1", "/sub/b.txt", b"world!")
    assert sorted(await fs.stat_all("ws-1")) == [("/a.txt", 5), ("/sub/b.txt", 6)]


async def test_stat_all_filters_by_prefix(fs: MemoryFileStore):
    await fs.write("ws-1", "/src/a.py", b"aa")
    await fs.write("ws-1", "/README", b"r")
    assert await fs.stat_all("ws-1", "/src/") == [("/src/a.py", 2)]


async def test_exists(fs: MemoryFileStore):
    await fs.write("ws-1", "/a", b"a")
    assert await fs.exists("ws-1", "/a")
    assert not await fs.exists("ws-1", "/b")


async def test_delete_removes_file(fs: MemoryFileStore):
    await fs.write("ws-1", "/a", b"a")
    await fs.delete("ws-1", "/a")
    assert not await fs.exists("ws-1", "/a")


async def test_delete_missing_raises(fs: MemoryFileStore):
    with pytest.raises(FileNotFound):
        await fs.delete("ws-1", "/nope")


async def test_delete_in_unknown_workspace_raises(fs: MemoryFileStore):
    with pytest.raises(FileNotFound):
        await fs.delete("never-existed", "/nope")


async def test_workspaces_are_isolated(fs: MemoryFileStore):
    await fs.write("ws-1", "/a", b"first")
    await fs.write("ws-2", "/a", b"second")
    assert await fs.read("ws-1", "/a") == b"first"
    assert await fs.read("ws-2", "/a") == b"second"


async def test_read_in_unknown_workspace_raises(fs: MemoryFileStore):
    with pytest.raises(FileNotFound):
        await fs.read("never-existed", "/a")


async def test_ls_unknown_workspace_returns_empty(fs: MemoryFileStore):
    assert await fs.ls("never-existed") == []


# --- Honest directories: dirs are first-class, no .keep hack ---


async def test_write_creates_ancestor_dirs(fs: MemoryFileStore):
    await fs.write("ws", "/data/raw/x.csv", b"x")
    assert await fs.is_dir("ws", "/data")
    assert await fs.is_dir("ws", "/data/raw")
    assert not await fs.is_dir("ws", "/data/raw/x.csv")  # a file, not a dir


async def test_mkdir_creates_empty_dir_with_no_files(fs: MemoryFileStore):
    await fs.mkdir("ws", "/empty")
    assert await fs.is_dir("ws", "/empty")
    assert await fs.ls("ws") == []  # no placeholder file
    assert "/empty" in await fs.listdir("ws")


async def test_mkdir_creates_ancestors(fs: MemoryFileStore):
    await fs.mkdir("ws", "/a/b/c")
    assert await fs.is_dir("ws", "/a")
    assert await fs.is_dir("ws", "/a/b")
    assert await fs.is_dir("ws", "/a/b/c")


async def test_mkdir_is_idempotent(fs: MemoryFileStore):
    await fs.mkdir("ws", "/d")
    await fs.mkdir("ws", "/d")
    assert await fs.is_dir("ws", "/d")


async def test_mkdir_over_existing_file_raises(fs: MemoryFileStore):
    await fs.write("ws", "/d", b"x")
    with pytest.raises(FileExists):
        await fs.mkdir("ws", "/d")


async def test_deleting_last_file_keeps_the_dir(fs: MemoryFileStore):
    await fs.write("ws", "/d/a.txt", b"a")
    await fs.delete("ws", "/d/a.txt")
    assert await fs.is_dir("ws", "/d")  # empty dir survives — honest FS


async def test_rmdir_removes_the_whole_subtree(fs: MemoryFileStore):
    await fs.write("ws", "/d/a.txt", b"a")
    await fs.write("ws", "/d/sub/b.txt", b"b")
    await fs.mkdir("ws", "/d/empty")
    await fs.rmdir("ws", "/d")
    assert not await fs.is_dir("ws", "/d")
    assert not await fs.is_dir("ws", "/d/sub")
    assert not await fs.is_dir("ws", "/d/empty")
    assert not await fs.exists("ws", "/d/a.txt")
    assert not await fs.exists("ws", "/d/sub/b.txt")


async def test_rmdir_missing_raises(fs: MemoryFileStore):
    with pytest.raises(FileNotFound):
        await fs.rmdir("ws", "/nope")


async def test_listdir_returns_all_dirs(fs: MemoryFileStore):
    await fs.write("ws", "/a/b/x", b"1")
    await fs.mkdir("ws", "/c")
    assert sorted(await fs.listdir("ws")) == ["/a", "/a/b", "/c"]


async def test_dirs_are_isolated_per_workspace(fs: MemoryFileStore):
    await fs.mkdir("ws1", "/d")
    assert not await fs.is_dir("ws2", "/d")
