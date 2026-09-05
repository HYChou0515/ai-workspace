import shutil
from pathlib import Path

import pytest

from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.protocol import SandboxHandle, SandboxNotFound, SandboxSpec

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


async def test_base_reown_is_a_noop(sandbox: LocalProcessSandbox):
    # #504: the base backend owns everything it writes (single principal), so its
    # `reown` hook does nothing — only IsolatedProcessSandbox chowns a restored
    # tree to the sandbox uid. It must not raise on a live handle.
    h = await sandbox.create(SandboxSpec())
    assert await sandbox.reown(h) is None


async def test_workspace_dir_is_the_handles_root_subdir(sandbox: LocalProcessSandbox, tmp_path):
    """#492: the host rsyncs THIS dir to/from the NFS archive — it must be the
    same `root` subdir walk/file ops are scoped to (never the infra area)."""
    h = await sandbox.create(SandboxSpec())
    ws = sandbox.workspace_dir(h)
    assert ws == tmp_path / h.id / "root"
    assert ws.is_dir()


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
    entries = (await sandbox.walk(h, "/")).files
    by_path = {e.path: e.size for e in entries}
    assert by_path == {"/a.txt": 5, "/sub/b.txt": 7}
    # version is populated for real-FS impls (mtime+size stamp).
    assert all(e.version for e in entries)


async def test_walk_separates_directories_from_files(sandbox: LocalProcessSandbox):
    """Directories come back in their own half — never as file entries, which
    the mirror would try to download and the quota would bill."""
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x", "/a/b/c.txt")
    walked = await sandbox.walk(h, "/")
    assert [e.path for e in walked.files] == ["/a/b/c.txt"]
    assert sorted(walked.dirs) == ["/a", "/a/b"]


async def test_walk_reports_a_directory_that_holds_no_files(sandbox: LocalProcessSandbox):
    """The case that has no other witness: an empty folder is in no file path."""
    h = await sandbox.create(SandboxSpec())
    await sandbox.mkdir(h, "/empty")
    walked = await sandbox.walk(h, "/")
    assert walked.files == []
    assert walked.dirs == ["/empty"]


async def test_file_ops_exists_delete_mkdir_rmdir_rename(sandbox: LocalProcessSandbox):
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, b"x", "/src/a.txt")
    assert await sandbox.exists(h, "/src/a.txt") is True
    assert await sandbox.exists(h, "/missing") is False

    await sandbox.mkdir(h, "/d/e")  # empty dir, ancestors created
    await sandbox.rename(h, "/src", "/dst")
    assert {e.path for e in (await sandbox.walk(h, "/")).files} == {"/dst/a.txt"}

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
    assert all("infra" not in e.path for e in (await sb.walk(h, "/")).files)
    # while a normal (cwd-relative) output lands in the workspace and IS visible
    await sb.exec(h, ["sh", "-c", "echo out > made.txt"])
    assert "/made.txt" in {e.path for e in (await sb.walk(h, "/")).files}


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

from sandbox_host.local_process import _jail_argv, _userns_supported  # noqa: E402

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
    import sandbox_host.local_process as lp

    lp._userns_supported.cache_clear()

    def boom(*a, **k):
        raise FileNotFoundError("unshare not installed")

    monkeypatch.setattr(lp.subprocess, "run", boom)
    try:
        assert lp._userns_supported() is False
    finally:
        lp._userns_supported.cache_clear()


@_needs_userns
async def test_isolated_exec_has_the_workspace_as_cwd_but_not_as_home(tmp_path):
    """cwd is the workspace; `~` is deliberately NOT. #600 moved $HOME to the
    infra-area `/.home` so a tool's profile (LibreOffice's user installation)
    never lands on the mirrored, persisted workspace — this asserted the old
    arrangement, and only ever ran where unprivileged userns exists, so CI
    skipped it and the drift went unnoticed."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"voids=42\n", "/data.csv")
    rel = await sb.exec(h, ["cat", "data.csv"])  # cwd = workspace
    assert rel.exit_code == 0 and "voids=42" in rel.stdout.decode()
    home = await sb.exec(h, ["sh", "-c", "cd ~ && pwd"])
    assert home.exit_code == 0, home.stderr
    assert home.stdout.decode().strip() == "/.home"  # not the workspace


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
    d = base / "prebuilt" / "builtin" / "mytool"
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
    assert {e.path for e in (await sb.walk(h, "/")).files} == {"/note.md"}  # tools invisible


async def test_unjailed_tools_dir_is_symlinked_outside_workspace(tmp_path):
    """Unjailed: the tools dir is exposed via a symlink, reached from the
    workspace as ../.tools, and still invisible to walk."""
    tools = _fake_tool_dir(tmp_path)
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    run = await sb.exec(h, ["../.tools/mytool/run"])  # relative from cwd=workspace
    assert run.exit_code == 0 and "TOOL-OK" in run.stdout.decode()
    await sb.upload(h, b"u", "/note.md")
    assert {e.path for e in (await sb.walk(h, "/")).files} == {"/note.md"}  # tools invisible


@_needs_userns
async def test_isolated_exec_cleans_up_dev_scaffolding(tmp_path):
    """The jail's /dev device-node files must not leak back into the
    workspace listing (they're scaffolding, removed after each exec)."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x", "/note.md")
    await sb.exec(h, ["echo", "hi"])
    files = {e.path for e in (await sb.walk(h, "/")).files}
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
    stack = tools / "builtin" / "python-stack"
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
    stack = tools / "builtin" / "python-stack"
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
    (tools / "builtin").mkdir(parents=True)
    # the builtin tree is non-empty but contains NO python-stack subdir.
    (tools / "builtin" / "something-else").mkdir()

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
# Production pods run unjailed (no unprivileged userns — uid + cgroup is the
# isolation model), so the jail bootstrap's `python` → python-stack shim never
# fired there: `exec(["python", ...])` fell through to the host's own service
# venv. These tests pin the unjailed shim. No `@_needs_userns` — unjailed runs
# anywhere, which is exactly the point.


async def test_unjailed_python_shim_routes_to_python_stack_when_provisioned(tmp_path):
    """Unjailed, `python` must still route to the provisioned `python-stack`
    carrier's launcher — the agent's raw `exec(["python", ...])` then sees the
    bundle's pandas / numpy / pptx, not the bare host python. Fake the carrier
    with a sentinel-printing launch."""
    tools = tmp_path / "prebuilt"
    stack = tools / "builtin" / "python-stack"
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
    stack = tools / "builtin" / "python-stack"
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
    the host's own service venv (the head of the inherited PATH) — it falls
    back to /usr/bin/python3. #350's bug was exactly that fall-through to the
    host venv; the fallback shim is the regression lock."""
    import os
    import sys

    tools = tmp_path / "prebuilt"
    (tools / "builtin").mkdir(parents=True)
    (tools / "builtin" / "something-else").mkdir()  # present but NO python-stack

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
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x", "/note.md")
    r1 = await sb.exec(h, ["python", "-c", "print('ok')"])  # builds .jailbin
    r2 = await sb.exec(h, ["python", "-c", "print('ok')"])  # rebuild: must not raise
    assert r1.exit_code == 0 and r2.exit_code == 0
    assert r2.stdout.decode().strip() == "ok"
    assert {e.path for e in (await sb.walk(h, "/")).files} == {"/note.md"}  # shim invisible


async def test_unjailed_exec_sets_sandbox_home_to_private_per_sandbox_dir(tmp_path):
    """#393: unjailed exec exposes SANDBOX_HOME → a per-sandbox `.home` OUTSIDE
    the workspace. The carrier launcher routes HOME (and a user's `pip --user`
    install fallback) there — private to this sandbox, reaped with it, never a
    shared /tmp. Being a workspace sibling, walk never sees it."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["sh", "-c", "echo $SANDBOX_HOME"])
    assert r.exit_code == 0
    home = Path(r.stdout.decode().strip())
    assert home.name == ".home"
    assert home.is_dir()
    await sb.upload(h, b"x", "/note.md")
    assert {e.path for e in (await sb.walk(h, "/")).files} == {"/note.md"}  # .home invisible


async def test_a_plain_exec_gets_home_off_the_synced_workspace(tmp_path):
    """HOME for ANY exec is the per-sandbox `.home`, not the workspace — so a tool
    that writes its profile to $HOME (LibreOffice's user installation) doesn't
    land it on the mirrored/persisted workspace, where it can't create/lock and
    `soffice` aborts "User installation could not be completed". Mirror of the
    app-side change; completes #393 (which moved only the carrier's HOME)."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec())
    _argv, cwd, env = sb._exec_argv(h, ["true"])
    home = Path(env["HOME"])
    assert home == Path(cwd).parent / ".home"  # infra-area sibling, never synced
    assert home != Path(cwd)  # NOT the mirrored workspace
    assert home.is_dir()


async def test_an_exec_rebuilds_a_home_the_sandbox_never_got(tmp_path):
    """`.home` is made by `create` but USED by every exec, and a live sandbox
    never goes back through `create` — the app re-acquires only when its liveness
    probe says the sandbox is GONE. So one that predates this dir, or that an
    older image of this service built, runs for the rest of its life without it
    while every exec still points HOME there, and `soffice` aborts "User
    installation could not be completed" against a HOME that isn't a directory
    at all. Guaranteed at the point of use instead. Mirrors the app-side change."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec())
    home = sb._require(h) / ".home"
    home.rmdir()  # the state every sandbox built before the dir existed is in

    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    assert Path(env["HOME"]) == home
    assert home.is_dir()


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


async def test_readiness_marker_lives_outside_workspace_and_is_invisible_366(sandbox, tmp_path):
    # #366 P5: readiness is a first-class marker at the SANDBOX ROOT ($ROOT/id/
    # .ready) — a sibling of the workspace, never inside it. So it never shows
    # up in walk / a file listing (can't clutter the file tree or be faked by a
    # user file), yet any pod can still ask `is_ready`.
    h = await sandbox.create(SandboxSpec())
    assert await sandbox.is_ready(h) is False
    await sandbox.mark_ready(h)
    assert await sandbox.is_ready(h) is True

    # physically at the sandbox root, NOT the user workspace
    assert (tmp_path / h.id / ".ready").exists()
    assert not (tmp_path / h.id / "root" / ".ready").exists()
    # invisible to the workspace view — in BOTH halves of the traversal, so the
    # marker cannot surface as a folder either now that dirs are reported
    walked = await sandbox.walk(h, "/")
    assert walked.files == []
    assert walked.dirs == []
    assert await sandbox.exists(h, "/.ready") is False


async def test_kill_unlinks_ready_marker_before_rmtree_366(sandbox, tmp_path, monkeypatch):
    # #366 P4/P5: teardown must unlink `.ready` BEFORE the (arbitrarily-ordered)
    # rmtree, so a mirror racing the reap sees an incomplete sandbox and skips
    # its delete phase instead of wiping the durable snapshot. The marker now
    # lives at the sandbox root (out of the workspace).
    import sandbox_host.local_process as lp

    h = await sandbox.create(SandboxSpec())
    await sandbox.mark_ready(h)
    marker = tmp_path / h.id / ".ready"
    assert marker.exists()

    ready_gone_at_rmtree = {}
    real_rmtree = lp.shutil.rmtree

    def spy_rmtree(p, **kw):
        ready_gone_at_rmtree["v"] = not marker.exists()  # already unlinked?
        return real_rmtree(p, **kw)

    monkeypatch.setattr(lp.shutil, "rmtree", spy_rmtree)
    await sandbox.kill(h)
    assert ready_gone_at_rmtree["v"] is True  # marker unlinked BEFORE rmtree


async def test_the_sandbox_sees_the_builtin_tools_not_the_layout_around_them(tmp_path):
    """#674: the tools root now holds `builtin/` (baked into this image) beside
    `ext/` (third-party bundles, content-addressed). A sandbox must see the
    TOOLS — `/.tools/mytool` — never the layout, or every tool path in every
    prompt and every bundle would have to grow a `builtin/` in the middle."""
    root = tmp_path / "toolsroot"
    tool = root / "builtin" / "mytool"
    tool.mkdir(parents=True)
    (tool / "run").write_text("#!/bin/sh\necho TOOL-OK\n")
    (tool / "run").chmod(0o755)
    (root / "ext" / ("a" * 64)).mkdir(parents=True)

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=root)
    h = await sb.create(SandboxSpec())

    run = await sb.exec(h, ["../.tools/mytool/run"])
    assert run.exit_code == 0 and "TOOL-OK" in run.stdout.decode()
    listed = await sb.exec(h, ["ls", "../.tools"])
    names = listed.stdout.decode().split()
    assert "mytool" in names
    assert "builtin" not in names and "ext" not in names


def _tools_root_with(tmp_path, *, ext: dict[str, str]):
    """A tools layout: one first-party tool plus third-party bundles by sha."""
    root = tmp_path / "toolsroot"
    builtin = root / "builtin" / "mytool"
    builtin.mkdir(parents=True)
    (builtin / "run").write_text("#!/bin/sh\necho BUILTIN-OK\n")
    (builtin / "run").chmod(0o755)
    for sha, marker in ext.items():
        d = root / "ext" / sha
        d.mkdir(parents=True)
        (d / "launch").write_text(f"#!/bin/sh\necho {marker}\n")
        (d / "launch").chmod(0o755)
    return root


async def test_unjailed_a_third_party_bundle_appears_under_the_name_we_gave_it(tmp_path):
    """#674: the sandbox is handed `name -> sha`; what it SEES is the name.
    The sha is how the host stores it, and no tool path ever mentions it."""
    sha = "c" * 64
    root = _tools_root_with(tmp_path, ext={sha: "THIRD-PARTY-OK"})
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=root)

    h = await sb.create(SandboxSpec(tools={"wafer-history": sha}))

    third = await sb.exec(h, ["../.tools/wafer-history/launch"])
    first = await sb.exec(h, ["../.tools/mytool/run"])
    assert "THIRD-PARTY-OK" in third.stdout.decode()
    assert "BUILTIN-OK" in first.stdout.decode()  # first-party still there
    listed = await sb.exec(h, ["ls", "../.tools"])
    assert sorted(listed.stdout.decode().split()) == ["mytool", "wafer-history"]


@_needs_userns
async def test_jailed_a_third_party_bundle_is_mounted_read_only_under_its_name(tmp_path):
    sha = "c" * 64
    root = _tools_root_with(tmp_path, ext={sha: "THIRD-PARTY-OK"})
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True, tools_dir=root)

    h = await sb.create(SandboxSpec(tools={"wafer-history": sha}))

    third = await sb.exec(h, ["/.tools/wafer-history/launch"])
    assert third.exit_code == 0 and "THIRD-PARTY-OK" in third.stdout.decode()
    first = await sb.exec(h, ["/.tools/mytool/run"])
    assert first.exit_code == 0 and "BUILTIN-OK" in first.stdout.decode()
    # Read-only, like the first-party tools beside it.
    ro = await sb.exec(h, ["sh", "-c", "echo x > /.tools/wafer-history/hack; echo rc=$?"])
    assert "rc=0" not in ro.stdout.decode()


@_needs_userns
async def test_jailed_the_sandbox_cannot_add_a_tool_of_its_own(tmp_path):
    """The view is assembled by the host and then sealed. If the sandbox could
    write into /.tools it could plant a `python-stack/launch` and capture every
    later `python` the agent runs."""
    root = _tools_root_with(tmp_path, ext={})
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True, tools_dir=root)
    h = await sb.create(SandboxSpec())

    r = await sb.exec(h, ["sh", "-c", "mkdir -p /.tools/evil 2>&1; echo rc=$?"])

    assert "rc=0" not in r.stdout.decode()


async def test_a_declared_third_party_tool_wins_a_name_it_shares_with_a_builtin(tmp_path):
    """Registering that name was a deliberate act by an operator; shipping a
    tool under it was ours. The operator is the later authority — and silently
    ignoring their declaration would be the worse surprise."""
    sha = "d" * 64
    root = _tools_root_with(tmp_path, ext={sha: "OVERRIDDEN"})
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=root)

    h = await sb.create(SandboxSpec(tools={"mytool": sha}))

    r = await sb.exec(h, ["../.tools/mytool/launch"])
    assert "OVERRIDDEN" in r.stdout.decode()


async def test_stray_files_beside_the_builtin_tools_are_not_offered_as_tools(tmp_path):
    # A build stamp or a README next to the bundles is not a tool; linking it
    # would put a name in the sandbox that resolves to nothing runnable.
    root = _tools_root_with(tmp_path, ext={})
    (root / "builtin" / "README").write_text("not a tool\n")
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=root)

    h = await sb.create(SandboxSpec())

    listed = await sb.exec(h, ["ls", "../.tools"])
    assert listed.stdout.decode().split() == ["mytool"]


async def test_a_deployment_with_no_first_party_tools_still_gets_its_own(tmp_path):
    # `builtin/` is absent on a host that ships no bundled tools. That is a
    # configuration, not a fault: the third-party tools must still appear.
    sha = "e" * 64
    root = tmp_path / "toolsroot"
    ext = root / "ext" / sha
    ext.mkdir(parents=True)
    (ext / "launch").write_text("#!/bin/sh\necho ONLY-THIRD-PARTY\n")
    (ext / "launch").chmod(0o755)
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=root)

    h = await sb.create(SandboxSpec(tools={"wafer-history": sha}))

    r = await sb.exec(h, ["../.tools/wafer-history/launch"])
    assert "ONLY-THIRD-PARTY" in r.stdout.decode()


async def test_the_host_can_say_which_third_party_bundles_are_in_use(tmp_path):
    """What the cache sweep asks before it deletes anything. Read from the
    live views, not from a counter kept alongside them: a counter drifts when
    a sandbox dies unrecorded, and drifting the wrong way here means deleting
    a bundle out from under a running turn."""
    live, unused = "a" * 64, "b" * 64
    root = _tools_root_with(tmp_path, ext={live: "L", unused: "U"})
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=root)

    await sb.create(SandboxSpec(tools={"wafer-history": live}))

    assert sb.tools_in_use() == {live}


async def test_a_host_with_no_tools_configured_holds_nothing_in_use(tmp_path):
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=None)
    await sb.create(SandboxSpec())

    assert sb.tools_in_use() == set()


async def test_a_sandbox_from_before_the_upgrade_does_not_break_the_sweep(tmp_path):
    # A rolling upgrade leaves sandboxes created by the previous version, which
    # have no view directory. The sweep must skip them, not crash — a crashing
    # sweeper means the cache grows forever and nobody notices until the disk
    # fills.
    sha = "a" * 64
    root = _tools_root_with(tmp_path, ext={sha: "L"})
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=root)
    h = await sb.create(SandboxSpec(tools={"wafer-history": sha}))
    shutil.rmtree(sb._require(h) / ".tools-view")

    assert sb.tools_in_use() == set()


@_needs_userns
async def test_isolated_python_shim_prefers_the_workspaces_own_venv(tmp_path):
    """The jail bootstrap is the THIRD place that decides what `python` means,
    and it had two tiers while the other two had three: inside the jail, a
    workspace that declared its dependencies still got the carrier.

    The venv lives at the sandbox root, which IS the chroot root — so `/.venv`
    in here and `<root>/.venv` out there are the same directory, and nothing
    has to be bind-mounted for it to survive the exec.
    """
    tools = tmp_path / "prebuilt"
    # `builtin/`: this backend reads its tools dir through that layer, so a
    # carrier planted a level up is never found — and a test whose fallback
    # target does not exist proves nothing about the fallback.
    stack = tools / "builtin" / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    venv_bin = tmp_path / "sb" / h.id / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\necho ROUTED-TO-PROJECT-VENV\n")
    (venv_bin / "python").chmod(0o755)

    r = await sb.exec(h, ["python", "-c", "ignored"])
    assert r.exit_code == 0, r.stderr.decode()
    assert "ROUTED-TO-PROJECT-VENV" in r.stdout.decode()

    r3 = await sb.exec(h, ["python3", "-c", "ignored"])
    assert "ROUTED-TO-PROJECT-VENV" in r3.stdout.decode(), "agents type `python3` too"


@_needs_userns
async def test_the_jails_venv_shim_is_a_wrapper_not_a_link(tmp_path):
    """The shape matters and the routing test above cannot see it.

    Its fake venv `python` is a shell script, so a SYMLINK would route there
    just as well — and a symlink is precisely what breaks a real venv: CPython
    resolves its own path to find `pyvenv.cfg` and a link from outside resolves
    straight past it to the base interpreter. So ask the jail directly what
    shape its shim has.
    """
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True)
    h = await sb.create(SandboxSpec())
    venv_bin = tmp_path / "sb" / h.id / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\necho ok\n")
    (venv_bin / "python").chmod(0o755)

    r = await sb.exec(h, ["sh", "-c", "test -L /tmp/.jailbin/python && echo LINK || echo WRAPPER"])

    assert r.stdout.decode().strip() == "WRAPPER", "a link would resolve past the venv"


@_needs_userns
async def test_the_jail_exports_virtual_env_for_the_project_venv(tmp_path):
    """Same reason as unjailed: `uv pip install` reads VIRTUAL_ENV. The jail
    bootstrap is what probes for the venv here, so it is what exports it."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True)
    h = await sb.create(SandboxSpec())
    venv_bin = tmp_path / "sb" / h.id / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\necho ok\n")
    (venv_bin / "python").chmod(0o755)

    r = await sb.exec(h, ["sh", "-c", "echo [$VIRTUAL_ENV]"])

    assert r.stdout.decode().strip() == "[/.venv]"


@_needs_userns
async def test_the_jail_announces_no_virtual_env_when_there_is_none(tmp_path):
    """And never the server's own, which the inherited environment carries
    whenever the server was started under `uv run`."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True)
    h = await sb.create(SandboxSpec())

    r = await sb.exec(h, ["sh", "-c", "echo [$VIRTUAL_ENV]"])

    assert r.stdout.decode().strip() == "[]"


@_needs_userns
async def test_the_jail_refuses_a_venv_built_on_its_own_shim(tmp_path):
    """Same cycle as unjailed, same route: this bootstrap puts /tmp/.jailbin
    first on PATH, `uv sync` picks its base interpreter off PATH, and /tmp is a
    fresh tmpfs each exec — so next time the shim is rebuilt as tier 1 pointing
    into a venv that points back at it, and `python` execs itself forever.

    Shell cannot walk the link chain hop by hop, so the guard reads the venv's
    own record of what it was built on: `pyvenv.cfg`'s `home =`.
    """
    tools = tmp_path / "prebuilt"
    # `builtin/`: this backend reads its tools dir through that layer, so a
    # carrier planted a level up is never found — and a test whose fallback
    # target does not exist proves nothing about the fallback.
    stack = tools / "builtin" / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    venv = tmp_path / "sb" / h.id / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\necho ROUTED-TO-PROJECT-VENV\n")
    (venv / "bin" / "python").chmod(0o755)
    (venv / "pyvenv.cfg").write_text("home = /tmp/.jailbin\nversion_info = 3.12.0\n")

    r = await sb.exec(h, ["python", "-c", "ignored"])

    assert r.exit_code == 0, r.stderr.decode()
    assert "ROUTED-TO-PYTHON-STACK" in r.stdout.decode(), (
        "a venv built on the shim must fall back, not loop"
    )


async def test_an_item_id_that_would_escape_the_cache_root_is_refused(tmp_path):
    """`item_id` arrives as a raw string in the POST body and becomes a path
    component. `mkdir(exist_ok=True)` accepts an EXISTING directory and
    `_own_cache` then chowns it 0700 to the sandbox uid — so `..` would hand an
    arbitrary directory on this host to one item's uid.

    The app-side twin has always validated it. This side only did when an NFS
    archive happened to be wired."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec(), item_id="../../escaped")

    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    cache = Path(env["UV_CACHE_DIR"]).resolve()
    root = (tmp_path / "sb").resolve()
    assert root in cache.parents, f"the cache escaped its root: {cache}"
    assert cache.name == h.id, "and an unusable id falls back to the handle"


def test_a_cache_that_cannot_shrink_says_so(tmp_path, caplog):
    """The sweep's own docstring promises it: "If everything left is in use,
    that is a host needing more disk, and it says so rather than breaking
    something." On THIS side that sentence was false for a while — the module
    had no logger at all, so the branch existed and printed nothing. And this is
    the deployment where it matters: the caches sit on the pod's ephemeral disk,
    which has no declared limit, so the kubelet evicts rather than the sweep
    reclaiming.
    """
    import logging

    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    cache = tmp_path / "sb" / ".uv-cache" / "busy"
    cache.mkdir(parents=True)
    (cache / "blob").write_bytes(b"x" * 8192)

    with caplog.at_level(logging.WARNING):
        removed = sb.sweep_uv_cache(in_use={"busy"}, max_bytes=1)

    assert removed == [], "a live item's cache is never evicted, however full"
    assert any("needs more disk" in r.message for r in caplog.records), [
        r.message for r in caplog.records
    ]
