import pytest

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxNotFound, SandboxSpec


@pytest.fixture
def sandbox(tmp_path) -> LocalProcessSandbox:
    return LocalProcessSandbox(root_dir=tmp_path)


async def test_create_returns_unique_handles(sandbox: LocalProcessSandbox):
    h1 = await sandbox.create(SandboxSpec())
    h2 = await sandbox.create(SandboxSpec())
    assert h1.id != h2.id


async def test_exec_real_echo(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    r = await sandbox.exec(h, ["echo", "hello"])
    assert r.exit_code == 0
    assert r.stdout == b"hello\n"


async def test_exec_false_returns_exit_1(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    r = await sandbox.exec(h, ["false"])
    assert r.exit_code == 1


async def test_upload_download_roundtrip(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"payload", "/notes.txt")
    assert await sandbox.download(h, "/notes.txt") == b"payload"


async def test_exec_cat_reads_uploaded_file(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"file content", "/data.txt")
    r = await sandbox.exec(h, ["cat", "data.txt"])
    assert r.exit_code == 0
    assert r.stdout == b"file content"


async def test_upload_nested_directory_creates_parents(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"deep", "/a/b/c.txt")
    assert await sandbox.download(h, "/a/b/c.txt") == b"deep"


async def test_kill_removes_workspace_dir(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x", "/x")
    await sandbox.kill(h)
    with pytest.raises(SandboxNotFound):
        await sandbox.exec(h, ["echo", "x"])


@pytest.mark.parametrize("op_name", ["exec", "upload", "download", "kill"])
async def test_op_on_unknown_handle_raises(sandbox: LocalProcessSandbox, op_name: str):
    fake = SandboxHandle(id="not-real")
    ops = {
        "exec": lambda: sandbox.exec(fake, ["echo", "x"]),
        "upload": lambda: sandbox.upload(fake, b"x", "/x"),
        "download": lambda: sandbox.download(fake, "/x"),
        "kill": lambda: sandbox.kill(fake),
    }
    with pytest.raises(SandboxNotFound):
        await ops[op_name]()


async def test_two_handles_have_isolated_fs(sandbox: LocalProcessSandbox):
    h1 = await sandbox.create(SandboxSpec())
    h2 = await sandbox.create(SandboxSpec())
    await sandbox.upload(h1, b"one", "/x")
    await sandbox.upload(h2, b"two", "/x")
    assert await sandbox.download(h1, "/x") == b"one"
    assert await sandbox.download(h2, "/x") == b"two"


async def test_walk_returns_files_relative_to_root(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"hello", "/a.txt")
    await sandbox.upload(h, b"world!!", "/sub/b.txt")
    entries = await sandbox.walk(h, "/")
    by_path = {e.path: e.size for e in entries}
    assert by_path == {"/a.txt": 5, "/sub/b.txt": 7}
    # mtime is populated for real-FS impls.
    assert all(e.mtime > 0 for e in entries)


async def test_walk_excludes_directories(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x", "/a/b/c.txt")
    entries = await sandbox.walk(h, "/")
    assert [e.path for e in entries] == ["/a/b/c.txt"]
