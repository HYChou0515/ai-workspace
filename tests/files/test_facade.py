"""WorkspaceFiles facade — P1: a single chokepoint for workspace file ops that
delegates to FileStore (behaviour unchanged). P2 flips its internals to route
by sandbox liveness; callers don't change."""

import pytest

from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.protocol import FileNotFound
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec

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


# ---------------- P2: liveness routing (warm→sandbox, cold→snapshot) ----------------


async def _wired() -> tuple[WorkspaceFiles, MemoryFileStore, MockSandbox, dict]:
    fs = MemoryFileStore()
    sb = MockSandbox()
    handle = {"h": None}  # mutate to simulate wake/idle-kill
    files = WorkspaceFiles(fs, sandbox=sb, handle_for=lambda _ws: handle["h"])
    return files, fs, sb, handle


async def test_cold_ops_hit_snapshot():
    files, fs, _sb, _h = await _wired()
    await files.write(WS, "/a.txt", b"cold")
    assert await fs.read(WS, "/a.txt") == b"cold"  # went to snapshot
    assert await files.read(WS, "/a.txt") == b"cold"
    assert await files.exists(WS, "/a.txt") is True
    assert await files.ls(WS) == ["/a.txt"]


async def test_warm_ops_hit_sandbox_not_snapshot():
    files, fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    await files.write(WS, "/b.txt", b"warm")
    # written to the sandbox, NOT the snapshot
    assert await sb.download(handle["h"], "/b.txt") == b"warm"
    assert await fs.exists(WS, "/b.txt") is False
    assert await files.read(WS, "/b.txt") == b"warm"
    assert await files.exists(WS, "/b.txt") is True
    assert await files.ls(WS) == ["/b.txt"]
    await files.delete(WS, "/b.txt")
    assert await files.exists(WS, "/b.txt") is False


async def test_warm_read_missing_maps_to_filenotfound():
    files, _fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    with pytest.raises(FileNotFound):
        await files.read(WS, "/nope")
    with pytest.raises(FileNotFound):
        await files.delete(WS, "/nope")


async def test_warm_is_dir_and_listdir_derived_from_walk():
    files, _fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    await files.write(WS, "/d/sub/f.txt", b"x")
    assert await files.is_dir(WS, "/d") is True
    assert await files.is_dir(WS, "/d/sub") is True
    assert await files.is_dir(WS, "/d/sub/f.txt") is False
    assert set(await files.listdir(WS)) == {"/d", "/d/sub"}


async def test_warm_mkdir_and_rmdir():
    files, fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    await files.mkdir(WS, "/d")  # no-op on the flat mock store, but routed warm
    await files.write(WS, "/d/f.txt", b"x")
    await files.rmdir(WS, "/d")  # routed to sandbox; removes the subtree
    assert await files.exists(WS, "/d/f.txt") is False
    assert await fs.exists(WS, "/d/f.txt") is False  # never touched the snapshot
    with pytest.raises(FileNotFound):
        await files.rmdir(WS, "/d")  # gone → FileNotFoundError mapped to FileNotFound
