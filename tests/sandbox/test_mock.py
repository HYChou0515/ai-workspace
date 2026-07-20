import pytest

from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxNotFound, SandboxSpec


async def test_create_returns_unique_handles():
    sandbox = MockSandbox()
    h1 = await sandbox.create(SandboxSpec())
    h2 = await sandbox.create(SandboxSpec())
    assert h1.id != h2.id


async def test_create_with_sandbox_id_is_stable_and_idempotent_345():
    # #345: a given sandbox_id pins the handle id AND reattaches to the same
    # underlying filesystem, so re-creating it does not wipe prior files (the
    # shared per-item working dir survives across turns/pods).
    sandbox = MockSandbox()
    h1 = await sandbox.create(SandboxSpec(), sandbox_id="item-abc")
    assert h1.id == "item-abc"
    await sandbox.upload(h1, b"keep me", "/notes.txt")
    h2 = await sandbox.create(SandboxSpec(), sandbox_id="item-abc")
    assert h2.id == "item-abc"
    assert await sandbox.download(h2, "/notes.txt") == b"keep me"


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


async def test_upload_file_download_to_file_roundtrip(tmp_path):
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    src = tmp_path / "src.bin"
    src.write_bytes(b"streamed-via-file")
    await sandbox.upload_file(h, src, "/tmp/x")
    assert await sandbox.download(h, "/tmp/x") == b"streamed-via-file"
    out = tmp_path / "out.bin"
    await sandbox.download_to_file(h, "/tmp/x", out)
    assert out.read_bytes() == b"streamed-via-file"


async def test_download_to_file_missing_raises(tmp_path):
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    with pytest.raises(FileNotFoundError):
        await sandbox.download_to_file(h, "/nope", tmp_path / "out.bin")


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


async def test_exec_sh_lc_runs_the_node_command_after_the_credential_export():
    """The workflow run wiring wraps a node's command as `sh -lc "export WF_TOKEN=…;
    <run>"`; the mock models that shell so a node behaves like the real one (echo → 0),
    which the default exit_code==0 gate now verifies (plan §2.2)."""
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    ok = await sandbox.exec(h, ["sh", "-lc", "export WF_TOKEN='t'; echo hello"])
    assert ok.exit_code == 0 and ok.stdout == b"hello\n"
    # a failing node command still surfaces its non-zero exit through the wrapper
    bad = await sandbox.exec(h, ["sh", "-lc", "export WF_TOKEN='t'; false"])
    assert bad.exit_code == 1
    # a bare export (no node command) succeeds
    empty = await sandbox.exec(h, ["sh", "-lc", "export WF_TOKEN='t'; "])
    assert empty.exit_code == 0


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


async def test_walk_version_changes_iff_content_changes():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"hello", "/a.txt")
    v1 = (await sandbox.walk(h, "/"))[0].version
    assert v1  # non-empty
    await sandbox.upload(h, b"hello", "/a.txt")  # same bytes
    assert (await sandbox.walk(h, "/"))[0].version == v1
    await sandbox.upload(h, b"changed", "/a.txt")
    assert (await sandbox.walk(h, "/"))[0].version != v1


async def test_exists_and_delete():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x", "/a.txt")
    assert await sandbox.exists(h, "/a.txt") is True
    assert await sandbox.exists(h, "/missing") is False
    await sandbox.delete(h, "/a.txt")
    assert await sandbox.exists(h, "/a.txt") is False
    with pytest.raises(FileNotFoundError):
        await sandbox.delete(h, "/a.txt")


async def test_mkdir_noop_and_rmdir_removes_subtree():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.mkdir(h, "/d/e")  # validates handle; empty dir unobservable
    await sandbox.upload(h, b"x", "/d/e/f.txt")
    await sandbox.rmdir(h, "/d")  # removes everything under /d
    assert await sandbox.exists(h, "/d/e/f.txt") is False
    with pytest.raises(FileNotFoundError):
        await sandbox.rmdir(h, "/d")  # nothing left under /d


async def test_rename_single_file():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x", "/a.txt")
    await sandbox.rename(h, "/a.txt", "/b.txt")
    assert await sandbox.exists(h, "/a.txt") is False
    assert await sandbox.download(h, "/b.txt") == b"x"


async def test_rename_file_and_subtree():
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x", "/src/a.txt")
    await sandbox.upload(h, b"y", "/src/sub/b.txt")
    await sandbox.rename(h, "/src", "/dst")
    paths = {e.path for e in await sandbox.walk(h, "/")}
    assert paths == {"/dst/a.txt", "/dst/sub/b.txt"}
    with pytest.raises(FileNotFoundError):
        await sandbox.rename(h, "/nope", "/x")


# ---- #366 P5: readiness marker (lives OUTSIDE the workspace) ----


async def test_readiness_toggles_and_is_invisible_to_the_workspace_366():
    # #366: `.ready` is an out-of-workspace marker, so it is a first-class
    # sandbox concept — NOT a file. A fresh sandbox is not ready; mark_ready
    # flips it; and it never leaks into the user's file listing (walk/exists),
    # which is exactly why it can't be seen in the file tree or fool the mirror.
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec())
    assert await sandbox.is_ready(h) is False
    await sandbox.mark_ready(h)
    assert await sandbox.is_ready(h) is True
    # invisible to the workspace: no phantom file appears anywhere
    assert await sandbox.walk(h, "/") == []
    assert await sandbox.exists(h, "/.ready") is False


async def test_readiness_ops_on_unknown_handle_raise_366():
    sandbox = MockSandbox()
    fake = SandboxHandle(id="never")
    with pytest.raises(SandboxNotFound):
        await sandbox.mark_ready(fake)
    with pytest.raises(SandboxNotFound):
        await sandbox.is_ready(fake)


async def test_kill_clears_readiness_so_a_recreated_sandbox_is_not_ready_366():
    # A reaped-then-recreated sandbox (dir rmtree'd, marker gone) must start
    # NOT ready — so the mirror won't trust a half-restored rebuild.
    sandbox = MockSandbox()
    h = await sandbox.create(SandboxSpec(), sandbox_id="item-x")
    await sandbox.mark_ready(h)
    await sandbox.kill(h)
    h2 = await sandbox.create(SandboxSpec(), sandbox_id="item-x")
    assert await sandbox.is_ready(h2) is False


async def test_disk_usage_totals_the_workspace():
    """#538: the quota asks the sandbox how big it is rather than walking it
    file-by-file — one number instead of an entry per file, and the answer comes
    from the thing that owns the disk, so every pod reading the same sandbox gets
    the same answer instead of each keeping its own tally."""
    sb = MockSandbox()
    h = await sb.create(SandboxSpec())
    assert await sb.disk_usage(h) == 0
    await sb.upload(h, b"x" * 30, "/a.bin")
    await sb.upload(h, b"y" * 12, "/sub/b.bin")
    assert await sb.disk_usage(h) == 42


async def test_disk_usage_counts_build_output_the_mirror_never_persists():
    """The quota caps the disk actually being consumed, so regenerable trees
    count: `node_modules/` really is on the volume, and it is what a runaway
    install fills it with. The mirror still refuses to persist them — that is a
    question about the durable archive, not about who is using the disk now."""
    sb = MockSandbox()
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x" * 10, "/keep.txt")
    await sb.upload(h, b"y" * 500, "/node_modules/dep/index.js")
    assert await sb.disk_usage(h) == 510


async def test_size_of_reports_one_file_without_walking():
    """The quota's overwrite credit: what the file being replaced currently
    costs. It must come from the same live source as the total, or the two
    halves of the subtraction disagree about whether a file counts."""
    sb = MockSandbox()
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x" * 30, "/a.bin")
    assert await sb.size_of(h, "/a.bin") == 30
    assert await sb.size_of(h, "/missing.bin") is None
