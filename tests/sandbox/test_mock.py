import pytest

from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxNotFound, SandboxSpec


async def test_create_returns_unique_handles():
    sandbox = MockSandbox()
    h1 = await sandbox.create(SandboxSpec())
    h2 = await sandbox.create(SandboxSpec())
    assert h1.id != h2.id


async def test_exec_echo_returns_stdout():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    result = await sandbox.exec(h, ["echo", "hi"])
    assert result.exit_code == 0
    assert result.stdout == b"hi\n"


async def test_upload_download_roundtrip():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"hello world", "/tmp/x")
    assert await sandbox.download(h, "/tmp/x") == b"hello world"


async def test_exec_cat_reads_uploaded_file():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"file content", "/notes/a.txt")
    result = await sandbox.exec(h, ["cat", "/notes/a.txt"])
    assert result.exit_code == 0
    assert result.stdout == b"file content"


async def test_exec_unknown_command_exits_127():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    result = await sandbox.exec(h, ["does-not-exist"])
    assert result.exit_code == 127
    assert b"unknown command" in result.stderr


async def test_exec_false_exits_1():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    result = await sandbox.exec(h, ["false"])
    assert result.exit_code == 1


async def test_exec_empty_cmd_exits_127():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    result = await sandbox.exec(h, [])
    assert result.exit_code == 127
    assert result.stderr


async def test_exec_cat_missing_file_exits_1():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    result = await sandbox.exec(h, ["cat", "/missing"])
    assert result.exit_code == 1
    assert b"/missing" in result.stderr


async def test_kill_then_exec_raises_sandbox_not_found():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.kill(h)
    with pytest.raises(SandboxNotFound):
        await sandbox.exec(h, ["echo", "x"])


@pytest.mark.parametrize("op_name", ["exec", "upload", "download", "kill"])
async def test_op_on_unknown_handle_raises(op_name: str) -> None:
    sandbox = MockSandbox()
    fake = SandboxHandle(id="not-a-real-id")
    ops = {
        "exec": lambda: sandbox.exec(fake, ["echo", "x"]),
        "upload": lambda: sandbox.upload(fake, b"x", "/x"),
        "download": lambda: sandbox.download(fake, "/x"),
        "kill": lambda: sandbox.kill(fake),
    }
    with pytest.raises(SandboxNotFound):
        await ops[op_name]()


async def test_two_handles_have_isolated_fs():
    sandbox = MockSandbox()
    h1 = await sandbox.create(SandboxSpec())
    h2 = await sandbox.create(SandboxSpec())
    await sandbox.upload(h1, b"one", "/x")
    await sandbox.upload(h2, b"two", "/x")
    assert await sandbox.download(h1, "/x") == b"one"
    assert await sandbox.download(h2, "/x") == b"two"


async def test_walk_returns_uploaded_files_with_size():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"hello", "/a.txt")
    await sandbox.upload(h, b"world!!", "/sub/b.txt")
    entries = await sandbox.walk(h, "/")
    by_path = {e.path: e.size for e in entries}
    assert by_path == {"/a.txt": 5, "/sub/b.txt": 7}


async def test_walk_on_unknown_handle_raises():
    sandbox = MockSandbox()
    with pytest.raises(SandboxNotFound):
        await sandbox.walk(SandboxHandle(id="never"), "/")


async def test_walk_with_non_root_prefix_filters_results():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"a", "/inside/a.txt")
    await sandbox.upload(h, b"b", "/elsewhere/b.txt")
    entries = await sandbox.walk(h, "/inside")
    assert [e.path for e in entries] == ["/inside/a.txt"]
