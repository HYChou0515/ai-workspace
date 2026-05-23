"""MemoryFileStore — in-process, no specstar dependency.

The contract mirrors SpecstarFileStore so callers can swap them in.
This test set is intentionally tight: write / read / ls / exists /
delete, plus dirty-path tracking, plus isolation across workspace_id.
"""

from __future__ import annotations

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.protocol import FileNotFound


@pytest.fixture
def fs() -> MemoryFileStore:
    return MemoryFileStore()


async def test_write_read_roundtrip(fs: MemoryFileStore):
    await fs.write("ws-1", "/a.txt", b"hello")
    assert await fs.read("ws-1", "/a.txt") == b"hello"


async def test_read_missing_raises(fs: MemoryFileStore):
    with pytest.raises(FileNotFound):
        await fs.read("ws-1", "/nope")


async def test_ls_filters_by_prefix(fs: MemoryFileStore):
    await fs.write("ws-1", "/src/a.py", b"a")
    await fs.write("ws-1", "/src/b.py", b"b")
    await fs.write("ws-1", "/README", b"r")
    assert sorted(await fs.ls("ws-1", "/src/")) == ["/src/a.py", "/src/b.py"]


async def test_ls_empty_prefix_returns_all(fs: MemoryFileStore):
    await fs.write("ws-1", "/a", b"a")
    await fs.write("ws-1", "/b", b"b")
    assert sorted(await fs.ls("ws-1")) == ["/a", "/b"]


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


async def test_dirty_paths_accumulate(fs: MemoryFileStore):
    await fs.write("ws-1", "/a", b"a")
    await fs.write("ws-1", "/b", b"b")
    assert fs.dirty_paths("ws-1") == {"/a", "/b"}


async def test_clear_dirty_resets(fs: MemoryFileStore):
    await fs.write("ws-1", "/a", b"a")
    fs.clear_dirty("ws-1")
    assert fs.dirty_paths("ws-1") == set()


async def test_clear_dirty_on_unknown_workspace_is_noop(fs: MemoryFileStore):
    fs.clear_dirty("never-existed")
    assert fs.dirty_paths("never-existed") == set()


async def test_workspaces_are_isolated(fs: MemoryFileStore):
    await fs.write("ws-1", "/a", b"first")
    await fs.write("ws-2", "/a", b"second")
    assert await fs.read("ws-1", "/a") == b"first"
    assert await fs.read("ws-2", "/a") == b"second"
    assert fs.dirty_paths("ws-1") == {"/a"}
    assert fs.dirty_paths("ws-2") == {"/a"}


async def test_read_in_unknown_workspace_raises(fs: MemoryFileStore):
    with pytest.raises(FileNotFound):
        await fs.read("never-existed", "/a")


async def test_ls_unknown_workspace_returns_empty(fs: MemoryFileStore):
    assert await fs.ls("never-existed") == []
