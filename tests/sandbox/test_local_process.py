from pathlib import Path

import pytest

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxNotFound, SandboxSpec

pytestmark = pytest.mark.integration


@pytest.fixture
def sandbox(tmp_path) -> LocalProcessSandbox:
    # Basic-mechanics tests exercise the plain (un-jailed) exec path so they
    # stay fast and don't depend on user-namespace support. Isolation has its
    # own dedicated tests below.
    return LocalProcessSandbox(root_dir=tmp_path, isolate=False)


async def test_create_returns_unique_handles(sandbox: LocalProcessSandbox):
    h1 = await sandbox.create(SandboxSpec())
    h2 = await sandbox.create(SandboxSpec())
    assert h1.id != h2.id


async def test_create_with_sandbox_id_is_deterministic_and_idempotent_345(tmp_path):
    # #345: the same sandbox_id maps to the same dir on the shared vol, and
    # re-creating it is idempotent (does not wipe files the agent left behind).
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = await sb.create(SandboxSpec(), sandbox_id="item-xyz")
    assert h.id == "item-xyz"
    assert (tmp_path / "item-xyz" / "root").is_dir()
    await sb.upload(h, b"left behind", "/a.txt")
    h2 = await sb.create(SandboxSpec(), sandbox_id="item-xyz")  # no FileExistsError
    assert await sb.download(h2, "/a.txt") == b"left behind"


async def test_second_instance_same_root_sees_files_345(tmp_path):
    # #345 core invariant: two LocalProcessSandbox instances (= two API pods)
    # sharing one root + the same item id see the SAME files — WITHOUT the
    # second instance ever calling create (it resolves the dir from the id).
    pod_a = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    pod_b = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = await pod_a.create(SandboxSpec(), sandbox_id="item-shared")
    await pod_a.upload(h, b"written by A", "/report.md")
    # pod_b never called create; it operates on the same handle/dir.
    assert await pod_b.download(SandboxHandle(id="item-shared"), "/report.md") == b"written by A"
    paths = {e.path for e in await pod_b.walk(SandboxHandle(id="item-shared"), "/")}
    assert "/report.md" in paths


async def test_create_rejects_unsafe_sandbox_id_345(sandbox: LocalProcessSandbox):
    # #345: the id becomes a path component on a shared vol — reject traversal /
    # separators so one item can't be steered into another's (or escape root).
    for bad in ["../evil", "a/b", "", ".", ".."]:
        with pytest.raises(ValueError):
            await sandbox.create(SandboxSpec(), sandbox_id=bad)


async def test_unknown_handle_dir_raises_sandbox_not_found_345(tmp_path):
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    with pytest.raises(SandboxNotFound):
        await sb.walk(SandboxHandle(id="never-created"), "/")


async def test_handle_for_id_derives_handle_or_none_for_unsafe_345(tmp_path):
    # #345: the sync routing path derives a handle from an id WITHOUT a session —
    # a safe id yields a handle (existence is not checked here), an unsafe id
    # yields None so it routes to the snapshot instead of raising.
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = sb.handle_for_id("item-1")
    assert h is not None and h.id == "item-1"
    for bad in ["../evil", "a/b", "", ".", ".."]:
        assert sb.handle_for_id(bad) is None


async def test_exec_real_echo(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    r = await sandbox.exec(h, ["echo", "hello"])
    assert r.exit_code == 0
    assert r.stdout == b"hello\n"


async def test_exec_false_returns_exit_1(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    r = await sandbox.exec(h, ["false"])
    assert r.exit_code == 1


async def test_exec_non_executable_returns_126(sandbox: LocalProcessSandbox, tmp_path):
    """A file that exists but isn't x-bit → POSIX exit 126 + stderr 'permission
    denied'. Distinguished from missing-binary (127)."""
    import os

    h = await sandbox.create(SandboxSpec())
    # Write a script INTO the workspace, no +x.
    workspace = tmp_path / h.id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    script = workspace / "noexec.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    os.chmod(script, 0o644)  # readable but not executable
    r = await sandbox.exec(h, [str(script)])
    assert r.exit_code == 126
    assert b"permission denied" in r.stderr.lower()


async def test_exec_unknown_command_returns_127(sandbox: LocalProcessSandbox):
    """Per protocol: a non-zero exit is RETURNED in exit_code, not raised.
    `create_subprocess_exec` raises FileNotFoundError when the binary doesn't
    exist; translate to POSIX's "command not found" exit 127 + stderr — so
    the terminal pane and /exec endpoint see a normal failure, not a 500."""
    h = await sandbox.create(SandboxSpec())
    r = await sandbox.exec(h, ["definitely-not-a-real-command-xyz"])
    assert r.exit_code == 127
    assert b"not found" in r.stderr.lower()
    assert b"definitely-not-a-real-command-xyz" in r.stderr


async def test_exec_times_out_instead_of_hanging(tmp_path):
    """A command that runs longer than the timeout is killed and returns a
    timeout result, so an interactive program (vim) can't freeze the
    terminal forever."""
    sandbox = LocalProcessSandbox(root_dir=tmp_path, exec_timeout=0.3)
    h = await sandbox.create(SandboxSpec())
    r = await sandbox.exec(h, ["sleep", "5"])
    assert r.exit_code != 0
    assert b"timed out" in r.stderr.lower()


async def test_exec_reads_eof_on_stdin(sandbox: LocalProcessSandbox):
    """stdin is /dev/null so a program reading stdin gets EOF rather than
    blocking on input it can never receive."""
    h = await sandbox.create(SandboxSpec())
    r = await sandbox.exec(h, ["cat"])  # cat with no args reads stdin → EOF
    assert r.exit_code == 0
    assert r.stdout == b""


async def test_upload_download_roundtrip(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"payload", "/notes.txt")
    assert await sandbox.download(h, "/notes.txt") == b"payload"


async def test_upload_file_download_to_file_roundtrip(sandbox: LocalProcessSandbox, tmp_path):
    h = await sandbox.create(SandboxSpec())
    src = tmp_path / "src.bin"
    src.write_bytes(b"streamed-payload" * 1000)
    await sandbox.upload_file(h, src, "/sub/big.bin")
    assert await sandbox.download(h, "/sub/big.bin") == b"streamed-payload" * 1000
    out = tmp_path / "out.bin"
    await sandbox.download_to_file(h, "/sub/big.bin", out)
    assert out.read_bytes() == b"streamed-payload" * 1000


async def test_download_to_file_missing_raises(sandbox: LocalProcessSandbox, tmp_path):
    h = await sandbox.create(SandboxSpec())
    with pytest.raises(FileNotFoundError):
        await sandbox.download_to_file(h, "/nope.bin", tmp_path / "out.bin")


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


async def test_readiness_marker_lives_outside_workspace_366(sandbox: LocalProcessSandbox, tmp_path):
    # #366: mark_ready flips is_ready; the marker sits at the SANDBOX ROOT
    # ($root/id/.ready), a sibling of the workspace — so walk/exists never see
    # it and it can't clutter the file tree.
    h = await sandbox.create(SandboxSpec())
    assert await sandbox.is_ready(h) is False
    await sandbox.mark_ready(h)
    assert await sandbox.is_ready(h) is True
    assert (tmp_path / h.id / ".ready").is_file()  # sandbox root
    assert not (tmp_path / h.id / "root" / ".ready").exists()  # not the workspace
    await sandbox.upload(h, b"x", "/a.txt")
    assert [e.path for e in await sandbox.walk(h, "/")] == ["/a.txt"]  # no /.ready
    assert await sandbox.exists(h, "/.ready") is False


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
    # version is populated for real-FS impls (mtime+size stamp).
    assert all(e.version for e in entries)


async def test_walk_excludes_directories(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x", "/a/b/c.txt")
    entries = await sandbox.walk(h, "/")
    assert [e.path for e in entries] == ["/a/b/c.txt"]


async def test_file_ops_exists_delete_mkdir_rmdir_rename(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x", "/src/a.txt")
    assert await sandbox.exists(h, "/src/a.txt") is True
    assert await sandbox.exists(h, "/missing") is False

    await sandbox.mkdir(h, "/d/e")  # empty dir, ancestors created
    await sandbox.rename(h, "/src", "/dst")
    assert {e.path for e in await sandbox.walk(h, "/")} == {"/dst/a.txt"}

    await sandbox.delete(h, "/dst/a.txt")
    assert await sandbox.exists(h, "/dst/a.txt") is False
    with pytest.raises(FileNotFoundError):
        await sandbox.delete(h, "/dst/a.txt")

    await sandbox.rmdir(h, "/d")
    with pytest.raises(FileNotFoundError):
        await sandbox.rmdir(h, "/d")
    with pytest.raises(FileNotFoundError):
        await sandbox.rename(h, "/nope", "/x")


# ---------------- Workspace boundary (~ vs infra) ----------------


async def test_workspace_is_a_subdir_so_infra_siblings_are_invisible(tmp_path):
    """The user's workspace is a SUBDIRECTORY of the sandbox dir (the agent's
    `~`/cwd). exec runs there, so an exec writing to the parent (the sandbox's
    infra area, where tools/caches live) is reachable but NOT part of the
    workspace — invisible to walk and never reverse-synced."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = await sb.create(SandboxSpec())
    pwd = (await sb.exec(h, ["pwd"])).stdout.decode().strip()
    assert Path(pwd).parent == (tmp_path / h.id)  # workspace is a child of the sandbox dir
    # a file in the sandbox dir (infra, sibling of the workspace) is reachable by
    # exec but is not part of the walked/synced workspace
    await sb.exec(h, ["sh", "-c", "echo infra > ../infra.txt"])
    assert (tmp_path / h.id / "infra.txt").exists()
    assert all("infra" not in e.path for e in await sb.walk(h, "/"))
    # while a normal (cwd-relative) output lands in the workspace and IS visible
    await sb.exec(h, ["sh", "-c", "echo out > made.txt"])
    assert "/made.txt" in {e.path for e in await sb.walk(h, "/")}


# ---------------- Live output streaming (on_output) ----------------


async def test_exec_streams_lines_to_on_output(tmp_path):
    """When given an on_output sink, exec streams stdout to it as it arrives
    and still returns the full output in the result."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = await sb.create(SandboxSpec())
    chunks: list[bytes] = []
    r = await sb.exec(h, ["sh", "-c", "echo a; echo b"], on_output=chunks.append)
    assert r.exit_code == 0
    assert b"".join(chunks) == b"a\nb\n"
    assert r.stdout == b"a\nb\n"


async def test_exec_streams_stderr_to_on_output_too(tmp_path):
    """A still-running tool's stderr (progress bars / warnings / logs) streams
    live to on_output as well, not only at the end (issue #23). stdout + stderr
    share the one live sink; the result still separates them."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = await sb.create(SandboxSpec())
    chunks: list[bytes] = []
    r = await sb.exec(h, ["sh", "-c", "echo out; echo err 1>&2"], on_output=chunks.append)
    assert r.exit_code == 0
    live = b"".join(chunks)
    assert b"out\n" in live and b"err\n" in live  # both reached the live sink
    assert r.stdout == b"out\n" and r.stderr == b"err\n"  # result still separated


async def test_exec_streaming_timeout_preserves_partial_stdout(tmp_path):
    """A long/looping command that times out keeps whatever it printed before
    the kill — both streamed and in the result (fixes the discard-on-timeout
    bug that left run history empty)."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False, exec_timeout=0.5)
    h = await sb.create(SandboxSpec())
    streamed: list[bytes] = []
    r = await sb.exec(h, ["sh", "-c", "echo first; sleep 5; echo never"], on_output=streamed.append)
    assert r.exit_code == 124
    assert b"first\n" in r.stdout
    assert b"never" not in r.stdout
    assert b"first\n" in b"".join(streamed)


async def test_exec_log_timeout_kills_silent_command(tmp_path):
    """#70: a command that goes silent longer than log_timeout is killed as
    hung — even though it's well within exec_timeout. Exit 124, a 'no output'
    notice, and the partial stdout kept."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False, exec_timeout=20, log_timeout=0.4)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["sh", "-c", "echo started; sleep 5; echo never"])
    assert r.exit_code == 124
    assert b"started\n" in r.stdout
    assert b"never" not in r.stdout
    assert b"no output" in r.stderr.lower()


async def test_exec_log_timeout_resets_on_output(tmp_path):
    """#70: steady output (each gap < log_timeout) keeps the command alive past
    log_timeout — the idle timer resets on every chunk, so a chatty long job
    isn't killed."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False, exec_timeout=20, log_timeout=0.5)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["sh", "-c", "for i in 1 2 3 4 5; do echo o$i; sleep 0.2; done"])
    assert r.exit_code == 0  # total ~1s > log_timeout, but never idle that long
    assert b"o5\n" in r.stdout


async def test_exec_with_both_timeouts_disabled_runs_to_completion(tmp_path):
    """#70: exec_timeout=0 AND log_timeout=0 disables both caps — the command
    runs to completion with no watchdog deadline."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False, exec_timeout=0, log_timeout=0)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["sh", "-c", "echo hi"])
    assert r.exit_code == 0
    assert b"hi\n" in r.stdout


async def test_exec_total_timeout_still_caps_even_with_output(tmp_path):
    """#70: the original exec_timeout (total wall-clock) still fires even when
    the command keeps producing output (so log_timeout never triggers). Its
    notice says 'total' to distinguish from a log timeout."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False, exec_timeout=0.5, log_timeout=20)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["sh", "-c", "while true; do echo x; sleep 0.05; done"])
    assert r.exit_code == 124
    assert b"total" in r.stderr.lower()


async def test_exec_kills_whole_process_group_on_cancel(tmp_path):
    """#74: cancelling the awaiting turn must kill the running command AND its
    detached grandchildren, not orphan them in the background. The command
    spawns a backgrounded `sleep` (grandchild), records its PID, then blocks;
    after the exec task is cancelled that PID must be dead — proving the whole
    process GROUP was killed, not just the direct child."""
    import asyncio
    import contextlib
    import os
    import time

    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = await sb.create(SandboxSpec())
    pidfile = tmp_path / "grandchild.pid"
    cmd = ["sh", "-c", f"sleep 30 & echo $! > '{pidfile}'; sleep 30"]
    task = asyncio.create_task(sb.exec(h, cmd))

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not (pidfile.exists() and pidfile.read_text().strip()):
        await asyncio.sleep(0.02)
    grandchild = int(pidfile.read_text().strip())

    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    assert alive(grandchild)  # the grandchild is running before we cancel
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):  # give the OS a moment to reap the killed group
            if not alive(grandchild):
                break
            await asyncio.sleep(0.02)
        assert not alive(grandchild), "background grandchild survived cancel (orphaned)"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(grandchild, 9)  # never leak the sleep if the assertion failed


# ---------------- Isolation (user-namespace + chroot jail) ----------------

from workspace_app.sandbox.local_process import _jail_argv, _userns_supported  # noqa: E402

_needs_userns = pytest.mark.skipif(
    not _userns_supported(), reason="unprivileged user namespaces unavailable"
)


def test_jail_argv_wraps_user_command_for_userns_chroot():
    """The wrapper drops privileges into a user+mount namespace, hands the
    sandbox root to the bootstrap, and runs the user command last."""
    argv = _jail_argv("/sb/root", ["python3", "/script.py"])
    assert argv[0] == "unshare"
    assert "--map-root-user" in argv and "--mount" in argv
    assert "/sb/root" in argv  # bootstrap receives the jail root
    assert argv[-2:] == ["python3", "/script.py"]  # user cmd at the tail


def test_default_isolate_is_resolved_to_a_bool(tmp_path):
    """isolate=None auto-detects userns support into a concrete bool."""
    sb = LocalProcessSandbox(root_dir=tmp_path)
    assert isinstance(sb._isolate, bool)


def test_userns_unsupported_when_unshare_unavailable(monkeypatch):
    """When `unshare` is missing or errors, detection reports no userns
    (so the sandbox falls back to plain, un-jailed exec)."""
    import workspace_app.sandbox.local_process as lp

    lp._userns_supported.cache_clear()

    def boom(*a, **k):
        raise FileNotFoundError("unshare not installed")

    monkeypatch.setattr(lp.subprocess, "run", boom)
    try:
        assert lp._userns_supported() is False
    finally:
        lp._userns_supported.cache_clear()


@_needs_userns
async def test_isolated_exec_workspace_is_cwd_and_home(tmp_path):
    """The workspace is the agent's cwd and $HOME (~): a file uploaded at
    /data.csv is read via a cwd-relative path and via ~ — not via the jail's
    `/` (which is now the infra root, not the workspace)."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"voids=42\n", "/data.csv")
    rel = await sb.exec(h, ["cat", "data.csv"])  # cwd = workspace
    assert rel.exit_code == 0 and "voids=42" in rel.stdout.decode()
    home = await sb.exec(h, ["sh", "-c", "cat ~/data.csv"])  # ~ = workspace
    assert home.exit_code == 0 and "voids=42" in home.stdout.decode()


@_needs_userns
async def test_isolated_exec_workspace_lists_user_files_and_hides_host(tmp_path):
    """The workspace (cwd) lists the user's files; the jail's `/` is the infra
    root (system mounts), and the host filesystem is not reachable."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x", "/note.md")
    here = (await sb.exec(h, ["ls", "."])).stdout.decode().split()  # cwd = workspace
    assert here == ["note.md"]  # only the user's file, no infra
    root = (await sb.exec(h, ["ls", "/"])).stdout.decode().split()  # infra root
    assert "home" not in root  # host /home is not reachable


def _fake_tool_dir(base):
    d = base / "prebuilt" / "mytool"
    d.mkdir(parents=True)
    (d / "run").write_text("#!/bin/sh\necho TOOL-OK\n")
    (d / "run").chmod(0o755)
    return base / "prebuilt"


@_needs_userns
async def test_isolated_tools_dir_is_mounted_read_only_outside_workspace(tmp_path):
    """A shared tools dir is bind-mounted read-only at /.tools (outside the
    workspace): runnable, not writable, and invisible to walk/sync."""
    tools = _fake_tool_dir(tmp_path)
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    run = await sb.exec(h, ["/.tools/mytool/run"])  # reachable + runnable
    assert run.exit_code == 0 and "TOOL-OK" in run.stdout.decode()
    ro = await sb.exec(h, ["sh", "-c", "echo x > /.tools/mytool/hack; echo rc=$?"])
    assert "rc=0" not in ro.stdout.decode()  # read-only → write fails
    await sb.upload(h, b"u", "/note.md")
    assert {e.path for e in await sb.walk(h, "/")} == {"/note.md"}  # tools invisible


async def test_unjailed_tools_dir_is_symlinked_outside_workspace(tmp_path):
    """Unjailed: the tools dir is exposed via a symlink, reached from the
    workspace as ../.tools, and still invisible to walk."""
    tools = _fake_tool_dir(tmp_path)
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    run = await sb.exec(h, ["../.tools/mytool/run"])  # relative from cwd=workspace
    assert run.exit_code == 0 and "TOOL-OK" in run.stdout.decode()
    await sb.upload(h, b"u", "/note.md")
    assert {e.path for e in await sb.walk(h, "/")} == {"/note.md"}  # tools invisible


@_needs_userns
async def test_isolated_exec_cleans_up_dev_scaffolding(tmp_path):
    """The jail's /dev device-node files must not leak back into the
    workspace listing (they're scaffolding, removed after each exec)."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x", "/note.md")
    await sb.exec(h, ["echo", "hi"])
    files = {e.path for e in await sb.walk(h, "/")}
    assert files == {"/note.md"}  # no /dev/null etc.


@_needs_userns
async def test_isolated_exec_python_is_python3(tmp_path):
    """`python` inside the jail must resolve to Python 3 — a Debian host's
    /usr/bin/python is often a legacy python2 symlink, which the jail would
    otherwise inherit (breaking f-strings and every py3-only script)."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["python", "-c", "import sys; print(sys.version_info.major)"])
    assert r.exit_code == 0
    assert r.stdout.decode().strip() == "3"


@_needs_userns
async def test_isolated_python_shim_prefers_python_stack_when_provisioned(tmp_path):
    """When the `python-stack` venv carrier is provisioned (its launcher
    bind-mounted at /.tools/python-stack/launch), the jail's `python`
    shim must route there — so the agent's raw `python` calls see the
    bundle's pandas / numpy / scipy / matplotlib instead of the bare
    host python with no data stack.

    Fake the carrier by writing a `launch` script that just prints a
    sentinel; if the shim routes correctly, `python anything` emits
    that sentinel.
    """
    tools = tmp_path / "prebuilt"
    stack = tools / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["python", "-c", "ignored"])
    assert r.exit_code == 0
    assert "ROUTED-TO-PYTHON-STACK" in r.stdout.decode()


@_needs_userns
async def test_isolated_python_shim_survives_bash_login_shell(tmp_path):
    """The agent commonly runs commands as `bash -lc "python3 -c …"` (login
    shell + command). A naive PATH export from the jail bootstrap is then
    clobbered by /etc/profile's hard-coded PATH on Debian/Ubuntu, dropping
    /tmp/.jailbin and routing `python3` back to the host's /usr/bin/python3
    — which has none of the python-stack carrier's data deps. Regression
    lock for the May 31 ModuleNotFoundError that fired in two consecutive
    investigations: the bootstrap's /etc/profile.d/jailbin.sh overlay must
    re-prepend the jailbin even under a login shell.
    """
    tools = tmp_path / "prebuilt"
    stack = tools / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    # `bash -lc` is the failure mode the user hit: login shell sources
    # /etc/profile, which on Debian sets PATH=/usr/local/sbin:/usr/local/bin:
    # /usr/sbin:/usr/bin:/sbin:/bin. Without our profile.d hook the python3
    # call would resolve to /usr/bin/python3 — the host Python, no pandas.
    r = await sb.exec(h, ["bash", "-lc", "python3 -c 'pass'"])
    assert r.exit_code == 0
    assert "ROUTED-TO-PYTHON-STACK" in r.stdout.decode()


@_needs_userns
async def test_isolated_python_shim_falls_back_to_host_python3_without_carrier(tmp_path):
    """Without a python-stack bundle in tools_dir, `python` must still
    work — it falls back to /usr/bin/python3. Regression lock so the
    new carrier-aware logic doesn't accidentally drop the fallback."""
    tools = tmp_path / "prebuilt"
    tools.mkdir()
    # tools_dir is non-empty but contains NO python-stack subdir.
    (tools / "something-else").mkdir()

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["python", "-c", "print(__import__('sys').version_info.major)"])
    assert r.exit_code == 0
    assert r.stdout.decode().strip() == "3"


@_needs_userns
async def test_isolated_exec_protects_host_usr_read_only(tmp_path):
    """System dirs are bind-mounted read-only — the agent can't tamper with
    the host's /usr from inside the jail."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["touch", "/usr/HACK"])
    assert r.exit_code != 0
    assert "read-only" in r.stderr.decode().lower()


# --- #350: the python shim must work UNJAILED too ----------------------------
# Our pods run unjailed (no unprivileged userns — SANDBOX_ISOLATE=false), so the
# jail bootstrap's `python` → python-stack shim never fired there: the agent's
# `exec(["python", ...])` fell through to the host's OWN venv. These tests pin
# the unjailed shim. No `@_needs_userns` — unjailed runs anywhere, the point.


async def test_unjailed_python_shim_routes_to_python_stack_when_provisioned(tmp_path):
    """Unjailed, `python` must still route to the provisioned `python-stack`
    carrier's launcher — so the agent's raw `exec(["python", ...])` sees the
    bundle's pandas / numpy / pptx, not the bare host python. Fake the carrier
    with a sentinel-printing launch."""
    tools = tmp_path / "prebuilt"
    stack = tools / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["python", "-c", "ignored"])
    assert r.exit_code == 0
    assert "ROUTED-TO-PYTHON-STACK" in r.stdout.decode()


async def test_unjailed_python3_flavour_also_routes_to_carrier(tmp_path):
    """Not only `python`: `python3` must route too, or `python3 -c …` (which
    agents commonly type) would fall through to the host interpreter."""
    tools = tmp_path / "prebuilt"
    stack = tools / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["python3", "-c", "ignored"])
    assert r.exit_code == 0
    assert "ROUTED-TO-PYTHON-STACK" in r.stdout.decode()


async def test_unjailed_python_shim_falls_back_to_host_python3_without_carrier(tmp_path):
    """Without a python-stack carrier, unjailed `python` must STILL not inherit
    the host's own venv (the head of the inherited PATH) — it falls back to
    /usr/bin/python3. #350's bug was exactly that fall-through; the fallback
    shim is the regression lock."""
    import os
    import sys

    tools = tmp_path / "prebuilt"
    tools.mkdir()
    (tools / "something-else").mkdir()  # tools dir present but NO python-stack

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["python", "-c", "import sys; print(sys.executable)"])
    assert r.exit_code == 0
    got = r.stdout.decode().strip()
    assert got != sys.executable  # did NOT inherit the venv this test runs under
    assert os.path.realpath(got) == os.path.realpath("/usr/bin/python3")


async def test_unjailed_python_shim_is_invisible_to_walk_and_idempotent(tmp_path):
    """The `.jailbin` shim lives outside the workspace, so walk never sees it
    even once built; and being rebuilt every exec must stay idempotent."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = await sb.create(SandboxSpec(), sandbox_id="pinned")
    await sb.upload(h, b"x", "/note.md")
    r1 = await sb.exec(h, ["python", "-c", "print('ok')"])  # builds .jailbin
    r2 = await sb.exec(h, ["python", "-c", "print('ok')"])  # rebuild: must not raise
    assert r1.exit_code == 0 and r2.exit_code == 0
    assert r2.stdout.decode().strip() == "ok"
    assert {e.path for e in await sb.walk(h, "/")} == {"/note.md"}  # shim invisible


async def test_unjailed_exec_sets_sandbox_home_to_private_per_sandbox_dir(tmp_path):
    """#393: unjailed exec exposes SANDBOX_HOME → a per-sandbox `.home` OUTSIDE
    the workspace. The carrier launcher routes HOME (and a user's `pip --user`
    install fallback) there — private to this sandbox, reaped with it, never a
    shared /tmp. Being a workspace sibling, walk never sees it."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec(), sandbox_id="pinned")
    r = await sb.exec(h, ["sh", "-c", "echo $SANDBOX_HOME"])
    assert r.exit_code == 0
    home = Path(r.stdout.decode().strip())
    assert home.name == ".home"
    assert home.is_dir()
    await sb.upload(h, b"x", "/note.md")
    assert {e.path for e in await sb.walk(h, "/")} == {"/note.md"}  # .home invisible


async def test_jailed_exec_passes_sandbox_home_tmp(tmp_path):
    """#393: the jail keeps HOME on its per-exec ephemeral /tmp (isolated
    there), passed EXPLICITLY as SANDBOX_HOME=/tmp rather than relied on as the
    launcher's silent default — keeping jail behavior byte-identical while the
    fail-safe default is reserved for genuine misconfiguration."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True)
    h = await sb.create(SandboxSpec())
    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    assert env["SANDBOX_HOME"] == "/tmp"


async def test_unjailed_python_shim_repoints_when_carrier_appears_after_fallback(tmp_path):
    """A carrier provisioned AFTER the first exec (the `provision_tools` path)
    must be picked up: the per-exec shim re-points `python` from the
    /usr/bin/python3 fallback to the carrier. Covers the mismatch→re-point path
    without a real bundle build."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec())
    r1 = await sb.exec(h, ["python", "-c", "print('fallback')"])  # no carrier yet
    assert r1.exit_code == 0 and r1.stdout.decode().strip() == "fallback"
    # A carrier lands in-sandbox after create (mimics provision_tools extract).
    stack = tmp_path / "sb" / h.id / ".tools" / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)
    r2 = await sb.exec(h, ["python", "-c", "ignored"])
    assert r2.exit_code == 0
    assert "ROUTED-TO-PYTHON-STACK" in r2.stdout.decode()


# ─── #393 end-to-end: a user's package install stays in the per-sandbox .home ──


@pytest.fixture(scope="module")
def carrier_tools(tmp_path_factory):
    """A real `python-stack` carrier built via uv, with its site-packages made
    read-only — simulating the pod's root-owned carrier that a dropped uid
    can't write, so `pip install --break-system-packages X` falls back to
    `--user` = $HOME/.local (the exact #393 path). Perms restored on teardown
    so the tmp tree can be cleaned up."""
    import os
    import shutil
    import subprocess

    if shutil.which("uv") is None:
        pytest.skip("uv not available")

    base = tmp_path_factory.mktemp("carrier")
    src = base / "src" / "python-stack"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        '[project]\nname = "python-stack"\nversion = "0.1.0"\n'
        'requires-python = ">=3.10"\ndependencies = []\n\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["src/python_stack"]\n'
    )
    pkg = src / "src" / "python_stack"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    subprocess.run(["uv", "lock", "--directory", str(src)], check=True)

    from workspace_app.tooling.prebuild import build_package

    tools = base / "tools"
    build_package(name="python-stack", source=src, dst=tools / "python-stack")
    site_dirs = list((tools / "python-stack").rglob("site-packages"))
    for sp in site_dirs:
        os.chmod(sp, 0o555)
    yield tools
    for sp in site_dirs:
        os.chmod(sp, 0o755)


async def test_unjailed_carrier_user_site_is_per_sandbox_home(tmp_path, carrier_tools):
    """#393 end-to-end (offline): the exec path's SANDBOX_HOME must flow through
    the carrier shim → launcher → the python's user-site. So the `pip --user`
    fallback target (where a user's install lands on the root-owned carrier) is
    THIS sandbox's `.home/.local`, distinct per sandbox, never a shared /tmp."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=carrier_tools)
    a = await sb.create(SandboxSpec(), sandbox_id="A")
    b = await sb.create(SandboxSpec(), sandbox_id="B")
    code = "import site; print(site.getusersitepackages())"
    ra = await sb.exec(a, ["python", "-c", code])
    rb = await sb.exec(b, ["python", "-c", code])
    assert ra.exit_code == 0, ra.stderr.decode()
    assert rb.exit_code == 0, rb.stderr.decode()
    a_site = ra.stdout.decode().strip()
    b_site = rb.stdout.decode().strip()
    assert a_site.startswith(str(sb._require(a) / ".home"))  # A's install → A's .home
    assert b_site.startswith(str(sb._require(b) / ".home"))  # B's → B's own .home
    assert a_site != b_site  # not a shared location
    assert "/tmp/.local" not in a_site  # never the shared pod /tmp


async def test_unjailed_pip_install_stays_in_home_and_other_sandbox_cannot_see_it(
    tmp_path, carrier_tools
):
    """#393 faithful reproduction: `pip install --break-system-packages cowsay`
    in an unjailed sandbox lands in THIS sandbox's `.home/.local` (the carrier
    is read-only → pip --user), and a SECOND sandbox — which installed nothing —
    cannot import it. This is the pod-wide leak the fix closes, reproduced
    through the real exec path. Needs network (PyPI)."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=carrier_tools)
    a = await sb.create(SandboxSpec(), sandbox_id="A")
    r = await sb.exec(
        a,
        [
            "python",
            "-m",
            "pip",
            "install",
            "--break-system-packages",
            "--no-input",
            "--disable-pip-version-check",
            "cowsay",
        ],
    )
    assert r.exit_code == 0, r.stderr.decode()
    a_home = sb._require(a) / ".home"
    # Installed into THIS sandbox's private .home, reaped with the sandbox.
    assert any(p.name == "cowsay" for p in (a_home / ".local").rglob("cowsay")), sorted(
        str(p) for p in a_home.rglob("*")
    )
    # .home is outside the workspace → invisible to the file tree.
    assert {e.path for e in await sb.walk(a, "/")} == set()
    # A fresh sandbox never sees A's install (no cross-sandbox leak).
    b = await sb.create(SandboxSpec(), sandbox_id="B")
    rb = await sb.exec(b, ["python", "-c", "import cowsay"])
    assert rb.exit_code != 0


async def test_disk_usage_grows_by_exactly_what_was_added(sandbox: LocalProcessSandbox):
    """`du` counts the directory entries too, so the absolute figure sits a few
    KB above the sum of file sizes — real disk, and noise against a GiB quota.
    What has to be exact is the DELTA: adding n bytes costs n."""
    h = await sandbox.create(SandboxSpec())
    before = await sandbox.disk_usage(h)
    await sandbox.upload(h, b"x" * 4096, "/a.bin")
    assert await sandbox.disk_usage(h) - before == 4096


async def test_disk_usage_counts_the_per_sandbox_home_but_not_system_files(
    sandbox: LocalProcessSandbox,
):
    """What the USER put here counts, wherever they put it. A
    `pip install --user` lands in the per-sandbox `.home` and really does occupy
    the volume, so it is theirs even though the file tree never shows it. The
    rest of the item dir is not: the readiness marker and the jail launcher are
    system, and `.tools` is a symlink to a tree every sandbox shares — charging
    that to each of them would bill the same bytes over and over."""
    h = await sandbox.create(SandboxSpec())
    await sandbox.mark_ready(h)
    before = await sandbox.disk_usage(h)

    item = Path(sandbox._require(h))
    (item / ".home" / "lib").mkdir(parents=True, exist_ok=True)
    (item / ".home" / "lib" / "big.whl").write_bytes(b"z" * 4096)
    assert await sandbox.disk_usage(h) - before >= 4096  # HOME is charged

    after_home = await sandbox.disk_usage(h)
    (item / ".jailbin").mkdir(exist_ok=True)
    (item / ".jailbin" / "launcher").write_bytes(b"s" * 4096)
    assert await sandbox.disk_usage(h) == after_home  # system files are not


async def test_disk_usage_does_not_follow_a_symlink_out_of_the_workspace(
    sandbox: LocalProcessSandbox, tmp_path: Path
):
    """Otherwise dropping one link into the workspace charges an entire tree the
    agent doesn't own — or the same bytes twice."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "huge.bin").write_bytes(b"q" * 100_000)
    h = await sandbox.create(SandboxSpec())
    before = await sandbox.disk_usage(h)
    (Path(sandbox._require(h)) / "root" / "link").symlink_to(elsewhere)

    assert await sandbox.disk_usage(h) - before < 1000  # the link itself, not its target


async def test_disk_usage_falls_back_to_walking_when_du_is_unavailable(
    sandbox: LocalProcessSandbox, monkeypatch
):
    """A minimal image may ship no coreutils. Reporting 0 there would silently
    disable the quota, so the traversal stays as a fallback."""
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x" * 4096, "/a.bin")

    async def _no_du(_targets):
        return None

    monkeypatch.setattr(sandbox, "_du", _no_du)
    assert await sandbox.disk_usage(h) == 4096  # the walk counts files only


async def test_size_of_reports_one_file(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x" * 30, "/a.bin")
    assert await sandbox.size_of(h, "/a.bin") == 30
    assert await sandbox.size_of(h, "/missing.bin") is None
    await sandbox.mkdir(h, "/adir")
    assert await sandbox.size_of(h, "/adir") is None  # directories are not files
