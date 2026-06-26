"""MockSandbox — the in-memory test double used by the wire-server tests.

Exercised directly here so every branch is covered without the wire layer."""

from __future__ import annotations

import pytest

from sandbox_host.mock import MockSandbox
from sandbox_host.protocol import SandboxHandle, SandboxNotFound, SandboxSpec


@pytest.fixture
async def sb():
    return MockSandbox()


async def test_unknown_handle_raises(sb: MockSandbox):
    with pytest.raises(SandboxNotFound):
        await sb.exists(SandboxHandle(id="nope"), "/x")


async def test_kill_then_use_raises(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    await sb.kill(h)
    with pytest.raises(SandboxNotFound):
        await sb.kill(h)


async def test_exec_echo_streams_to_sink(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    chunks: list[bytes] = []
    r = await sb.exec(h, ["echo", "a", "b"], on_output=chunks.append)
    assert r.exit_code == 0 and r.stdout == b"a b\n"
    assert chunks == [b"a b\n"]


async def test_exec_echo_without_sink(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["echo", "x"])
    assert r.stdout == b"x\n"


async def test_exec_cat_hit_and_miss(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"data", "/f")
    assert (await sb.exec(h, ["cat", "/f"])).stdout == b"data"
    miss = await sb.exec(h, ["cat", "/nope"])
    assert miss.exit_code == 1 and b"No such file" in miss.stderr


async def test_exec_false_unknown_and_empty(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    assert (await sb.exec(h, ["false"])).exit_code == 1
    assert (await sb.exec(h, ["bogus"])).exit_code == 127
    assert (await sb.exec(h, [])).exit_code == 127


async def test_download_missing_raises(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    with pytest.raises(FileNotFoundError):
        await sb.download(h, "/nope")


async def test_walk_root_and_subdir(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"a", "/dir/a")
    await sb.upload(h, b"bb", "/top")
    assert {e.path for e in await sb.walk(h, "/")} == {"/dir/a", "/top"}
    assert {e.path for e in await sb.walk(h, "/dir")} == {"/dir/a"}


async def test_delete_hit_and_miss(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x", "/a")
    await sb.delete(h, "/a")
    assert await sb.exists(h, "/a") is False
    with pytest.raises(FileNotFoundError):
        await sb.delete(h, "/a")


async def test_mkdir_is_noop_but_validates_handle(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    await sb.mkdir(h, "/d")  # no raise
    with pytest.raises(SandboxNotFound):
        await sb.mkdir(SandboxHandle(id="nope"), "/d")


async def test_rmdir_subtree_and_miss(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x", "/d/a")
    await sb.rmdir(h, "/d")
    assert await sb.exists(h, "/d/a") is False
    with pytest.raises(FileNotFoundError):
        await sb.rmdir(h, "/d")


async def test_rename_file_subtree_and_miss(sb: MockSandbox):
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x", "/a")
    await sb.rename(h, "/a", "/b")
    assert await sb.download(h, "/b") == b"x"
    await sb.upload(h, b"y", "/d/c")
    await sb.rename(h, "/d", "/e")
    assert await sb.download(h, "/e/c") == b"y"
    with pytest.raises(FileNotFoundError):
        await sb.rename(h, "/nope", "/z")
