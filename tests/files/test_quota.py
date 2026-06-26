"""Quota accounting through the WorkspaceFiles facade (#245). `remaining_quota`
is the per-write headroom the upload/edit endpoints gate on — an overwrite is a
*replace* (delta), and a disabled quota (0) returns None."""

from workspace_app.files.facade import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _files() -> WorkspaceFiles:
    return WorkspaceFiles(MemoryFileStore())


async def test_empty_workspace_has_full_headroom():
    files = _files()
    assert await files.remaining_quota("ws1", "/a", quota=1000) == 1000


async def test_headroom_shrinks_by_existing_files():
    files = _files()
    await files.write("ws1", "/a", b"x" * 300)
    # a *new* path sees the workspace's used bytes subtracted
    assert await files.remaining_quota("ws1", "/b", quota=1000) == 700


async def test_overwrite_credits_back_the_old_size():
    files = _files()
    await files.write("ws1", "/a", b"x" * 300)
    await files.write("ws1", "/b", b"y" * 200)  # used = 500
    # overwriting /a: its 300 is credited back, headroom = 1000 - (500 - 300)
    assert await files.remaining_quota("ws1", "/a", quota=1000) == 800


async def test_quota_zero_disables_the_cap():
    files = _files()
    await files.write("ws1", "/a", b"x" * 300)
    assert await files.remaining_quota("ws1", "/a", quota=0) is None


async def test_headroom_goes_negative_when_already_over():
    files = _files()
    await files.write("ws1", "/a", b"x" * 1500)
    assert await files.remaining_quota("ws1", "/b", quota=1000) == -500


class _NoUsageStore:
    """A FileStore without usage accounting — like the wiki-page store, which
    is never quota-gated. `workspace_usage` / `file_size` are duck-typed, so the
    facade falls back gracefully instead of crashing."""


async def test_store_without_usage_accounting_falls_back():
    files = WorkspaceFiles(_NoUsageStore())  # ty: ignore[invalid-argument-type]
    assert await files.workspace_usage("ws") == 0
    assert await files.file_size("ws", "/a") is None
    # remaining is then just the whole quota (nothing counted against it)
    assert await files.remaining_quota("ws", "/a", quota=1000) == 1000
