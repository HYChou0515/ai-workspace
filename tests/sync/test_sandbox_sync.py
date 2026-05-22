from datetime import UTC, datetime

import pytest
from specstar import SpecStar

from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec
from workspace_app.sync import DEFAULT_IGNORES, SandboxSync, should_ignore


@pytest.fixture
def fs() -> SpecstarFileStore:
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
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


# ---- flush ----


async def test_flush_uploads_dirty_paths_and_clears(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await fs.write("ws", "/notes.txt", b"first")
    assert fs.dirty_paths("ws") == {"/notes.txt"}

    n = await sync.flush("ws", h)
    assert n == 1
    assert await sandbox.download(h, "/notes.txt") == b"first"
    assert fs.dirty_paths("ws") == set()


async def test_flush_with_no_dirty_paths_is_noop(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    assert await sync.flush("ws", h) == 0


# ---- reverse ----


async def test_reverse_downloads_new_sandbox_files_into_filestore(
    fs: SpecstarFileStore, sandbox: MockSandbox
):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    # Simulate the agent's shell writing files inside the sandbox.
    await sandbox.upload(h, b"shell out", "/build/out.txt")

    n = await sync.reverse("ws", h)
    assert n == 1
    assert await fs.read("ws", "/build/out.txt") == b"shell out"
    # Reverse-sync's own writes shouldn't leave dirty marks behind.
    assert fs.dirty_paths("ws") == set()


async def test_reverse_skips_unchanged_files(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    # Same content on both sides.
    await fs.write("ws", "/same.txt", b"same")
    await sandbox.upload(h, b"same", "/same.txt")
    fs.clear_dirty("ws")

    n = await sync.reverse("ws", h)
    assert n == 0


async def test_reverse_updates_changed_files(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await fs.write("ws", "/x.txt", b"old")
    await sandbox.upload(h, b"new", "/x.txt")
    fs.clear_dirty("ws")

    n = await sync.reverse("ws", h)
    assert n == 1
    assert await fs.read("ws", "/x.txt") == b"new"


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


async def test_reverse_skips_ignored_paths(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.upload(h, b"x", "/src/main.py")
    await sandbox.upload(h, b"y", "/__pycache__/main.cpython-312.pyc")
    await sandbox.upload(h, b"z", "/.venv/bin/python")

    await sync.reverse("ws", h)
    assert await fs.ls("ws") == ["/src/main.py"]
