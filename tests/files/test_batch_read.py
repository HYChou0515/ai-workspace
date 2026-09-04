"""The batch read is a FAST LANE, not an interface (#781 Phase 3).

A sandbox that can hand back many files in one call is used when it is there and
ignored when it is not, and no caller is told which happened. So the tests come
in pairs: the same behaviour asserted with the lane open and with it closed, and
only the number of round trips allowed to differ. A behaviour that is only
correct on one of the two paths is the defect this shape exists to catch.
"""

from __future__ import annotations

import pytest

from workspace_app.files.facade import WorkspaceFiles, read_all, read_all_existing
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.protocol import FileNotFound
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec


class _CountingSandbox(MockSandbox):
    """Counts what a read costs: single downloads, and batched ones."""

    def __init__(self) -> None:
        super().__init__()
        self.downloads = 0
        self.batches = 0

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        self.downloads += 1
        return await super().download(handle, remote_path)


class _BatchingSandbox(_CountingSandbox):
    """A sandbox that CAN answer for many paths at once — the fast lane."""

    async def download_many(
        self, handle: SandboxHandle, remote_paths: list[str]
    ) -> list[bytes | None]:
        self.batches += 1
        out: list[bytes | None] = []
        for path in remote_paths:
            try:
                out.append(await MockSandbox.download(self, handle, path))
            except FileNotFoundError:
                out.append(None)  # absent is an ANSWER, not a failed batch
        return out


async def _files(sb: MockSandbox) -> WorkspaceFiles:
    handle = await sb.create(SandboxSpec())

    async def _resolve(_ws: str) -> SandboxHandle:
        return handle

    files = WorkspaceFiles(MemoryFileStore(), sandbox=sb, handle_for=_resolve)
    for i in range(30):
        await files.write("ws1", f"/r/{i}.md", f"body {i}".encode())
    return files


@pytest.mark.parametrize("sandbox_class", [_CountingSandbox, _BatchingSandbox])
async def test_the_same_bytes_come_back_in_order_either_way(sandbox_class) -> None:
    """The lane changes the number of round trips and nothing else — same bytes,
    same order."""
    sb = sandbox_class()
    files = await _files(sb)
    paths = [f"/r/{i}.md" for i in range(30)]

    got = await read_all(files, "ws1", paths)

    assert got == [f"body {i}".encode() for i in range(30)]


async def test_the_fast_lane_reads_thirty_files_in_one_round_trip() -> None:
    """What the lane is FOR. Without it, thirty files are thirty downloads."""
    slow, fast = _CountingSandbox(), _BatchingSandbox()
    slow_files, fast_files = await _files(slow), await _files(fast)
    paths = [f"/r/{i}.md" for i in range(30)]

    slow.downloads = fast.downloads = fast.batches = 0
    await read_all(slow_files, "ws1", paths)
    await read_all(fast_files, "ws1", paths)

    assert slow.downloads == 30
    assert (fast.batches, fast.downloads) == (1, 0)


@pytest.mark.parametrize("sandbox_class", [_CountingSandbox, _BatchingSandbox])
async def test_a_missing_path_is_an_error_either_way(sandbox_class) -> None:
    """`read_all` is the strict one: a caller that named a path it needs gets an
    error, whichever lane answered."""
    files = await _files(sandbox_class())

    with pytest.raises(FileNotFound):
        await read_all(files, "ws1", ["/r/0.md", "/r/gone.md"])


@pytest.mark.parametrize("sandbox_class", [_CountingSandbox, _BatchingSandbox])
async def test_a_missing_path_is_skipped_by_the_tolerant_read_either_way(sandbox_class) -> None:
    """`read_all_existing` is the listing one: a file deleted since the listing
    drops out and the rest still come back — on both lanes.

    This is the pair that matters most. Batching is exactly what could have
    turned one vanished file into an empty panel."""
    files = await _files(sandbox_class())

    got = await read_all_existing(files, "ws1", ["/r/0.md", "/r/gone.md", "/r/1.md"])

    assert got == {"/r/0.md": b"body 0", "/r/1.md": b"body 1"}
