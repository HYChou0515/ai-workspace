import pytest

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxNotFound, SandboxSpec


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
async def test_isolated_exec_resolves_absolute_workspace_path(tmp_path):
    """The agent's /-rooted paths (write_file convention) resolve inside the
    jail: a file uploaded at /data.csv is readable via an absolute path."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"voids=42\n", "/data.csv")
    r = await sb.exec(h, ["cat", "/data.csv"])
    assert r.exit_code == 0
    assert "voids=42" in r.stdout.decode()


@_needs_userns
async def test_isolated_exec_roots_at_sandbox_and_hides_host(tmp_path):
    """`/` inside the jail IS the sandbox (lists workspace files), and the
    host root is not visible."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    await sb.upload(h, b"x", "/note.md")
    listing = (await sb.exec(h, ["ls", "/"])).stdout.decode().split()
    assert "note.md" in listing
    assert "home" not in listing  # host /home is not reachable


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
async def test_isolated_exec_protects_host_usr_read_only(tmp_path):
    """System dirs are bind-mounted read-only — the agent can't tamper with
    the host's /usr from inside the jail."""
    sb = LocalProcessSandbox(root_dir=tmp_path, isolate=True)
    h = await sb.create(SandboxSpec())
    r = await sb.exec(h, ["touch", "/usr/HACK"])
    assert r.exit_code != 0
    assert "read-only" in r.stderr.decode().lower()
