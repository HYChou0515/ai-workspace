"""WorkspaceFiles facade — P1: a single chokepoint for workspace file ops that
delegates to FileStore (behaviour unchanged). P2 flips its internals to route
by sandbox liveness; callers don't change."""

import pytest

from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.protocol import FileNotFound

WS = "inv-1"


async def _files() -> WorkspaceFiles:
    return WorkspaceFiles(MemoryFileStore())


async def test_write_read_roundtrip():
    f = await _files()
    await f.write(WS, "/a.txt", b"hello")
    assert await f.read(WS, "/a.txt") == b"hello"
    assert await f.exists(WS, "/a.txt") is True
    assert await f.exists(WS, "/missing") is False


async def test_read_missing_raises():
    f = await _files()
    with pytest.raises(FileNotFound):
        await f.read(WS, "/nope")


async def test_ls_and_delete():
    f = await _files()
    await f.write(WS, "/a.txt", b"x")
    await f.write(WS, "/sub/b.txt", b"y")
    assert sorted(await f.ls(WS)) == ["/a.txt", "/sub/b.txt"]
    assert await f.ls(WS, "/sub") == ["/sub/b.txt"]
    await f.delete(WS, "/a.txt")
    assert await f.exists(WS, "/a.txt") is False


async def test_dirs_mkdir_listdir_is_dir_rmdir():
    f = await _files()
    await f.mkdir(WS, "/d/e")
    assert await f.is_dir(WS, "/d/e") is True
    assert "/d/e" in await f.listdir(WS)
    await f.rmdir(WS, "/d")
    assert await f.is_dir(WS, "/d") is False
