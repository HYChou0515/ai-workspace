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
