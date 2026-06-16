import pytest

from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec
from workspace_app.sync import DEFAULT_IGNORES, SandboxSync, should_ignore


@pytest.fixture
def fs() -> SpecstarFileStore:
    spec = make_spec(default_user="u")
    return SpecstarFileStore(spec)


@pytest.fixture
def sandbox() -> MockSandbox:
    return MockSandbox()


# ---- restore ----


async def test_restore_uploads_every_filestore_path_to_sandbox(
    fs: SpecstarFileStore, sandbox: MockSandbox
):
    await fs.write("ws", "/a.txt", b"A")
    await fs.write("ws", "/sub/b.txt", b"BB")
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    n = await sync.restore("ws", h)
    assert n == 2
    assert await sandbox.download(h, "/a.txt") == b"A"
    assert await sandbox.download(h, "/sub/b.txt") == b"BB"


async def test_restore_on_empty_workspace_is_a_noop(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    assert await sync.restore("never", h) == 0


async def test_restore_seeds_versions_so_first_mirror_is_noop(
    fs: SpecstarFileStore, sandbox: MockSandbox
):
    await fs.write("ws", "/a.txt", b"A")
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sync.restore("ws", h)
    # Nothing changed in the sandbox since the restore → mirror is a no-op.
    assert await sync.mirror("ws", h) == 0


# ---- mirror (PULL, version-diff, deletion-aware) ----


async def test_mirror_copies_new_sandbox_files_into_snapshot(
    fs: SpecstarFileStore, sandbox: MockSandbox
):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.upload(h, b"shell out", "/build/out.txt")  # shell-created file

    n = await sync.mirror("ws", h)
    assert n == 1
    assert await fs.read("ws", "/build/out.txt") == b"shell out"


async def test_mirror_skips_unchanged_via_version(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.upload(h, b"same", "/x.txt")
    assert await sync.mirror("ws", h) == 1  # first copy
    assert await sync.mirror("ws", h) == 0  # version unchanged → skipped


async def test_mirror_updates_changed_files(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.upload(h, b"old", "/x.txt")
    await sync.mirror("ws", h)
    await sandbox.upload(h, b"new", "/x.txt")  # content (version) changed
    assert await sync.mirror("ws", h) == 1
    assert await fs.read("ws", "/x.txt") == b"new"


async def test_mirror_propagates_deletions(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.upload(h, b"x", "/gone.txt")
    await sync.mirror("ws", h)
    assert await fs.exists("ws", "/gone.txt") is True

    await sandbox.delete(h, "/gone.txt")  # removed in the sandbox
    n = await sync.mirror("ws", h)
    assert n == 1  # one deletion
    assert await fs.exists("ws", "/gone.txt") is False


# ---- ignore list ----


@pytest.mark.parametrize(
    "path",
    [
        "/.venv/lib/python3.12/site.py",
        "/node_modules/react/index.js",
        "/sub/__pycache__/x.cpython-312.pyc",
        "/foo.pyc",
        "/.git/objects/12/abc",
        "/.pytest_cache/v/cache",
        "/.ruff_cache/0.15/abc",
    ],
)
def test_default_ignores_match(path: str):
    assert should_ignore(path, DEFAULT_IGNORES, size=10) is True


@pytest.mark.parametrize(
    "path",
    ["/src/main.py", "/README.md", "/data/x.json", "/.gitignore"],
)
def test_default_ignores_let_real_files_through(path: str):
    assert should_ignore(path, DEFAULT_IGNORES, size=10) is False


def test_ignore_rejects_files_over_size_cap():
    big = 11 * 1024 * 1024
    assert should_ignore("/totally_fine.bin", DEFAULT_IGNORES, size=big) is True


def test_ignore_literal_segment_pattern():
    """A pattern like 'secret' (no trailing /, no *.) matches a path
    segment with that exact name anywhere in the path."""
    assert should_ignore("/secret", ["secret"], size=10) is True
    assert should_ignore("/sub/secret", ["secret"], size=10) is True
    assert should_ignore("/not-a-secret", ["secret"], size=10) is False


async def test_mirror_skips_ignored_paths(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.upload(h, b"x", "/src/main.py")
    await sandbox.upload(h, b"y", "/__pycache__/main.cpython-312.pyc")
    await sandbox.upload(h, b"z", "/.venv/bin/python")

    await sync.mirror("ws", h)
    assert await fs.ls("ws") == ["/src/main.py"]
