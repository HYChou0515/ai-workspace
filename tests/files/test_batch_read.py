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
    """Counts what a read costs: single downloads, batched ones, and the
    per-operation liveness probe."""

    def __init__(self) -> None:
        super().__init__()
        self.downloads = 0
        self.batches = 0
        self.liveness_probes = 0

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        self.downloads += 1
        return await super().download(handle, remote_path)

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        if path == "/":
            self.liveness_probes += 1
        return await super().exists(handle, path)


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


class _ChunkRecordingStore(MemoryFileStore):
    """A durable store that CAN batch — and remembers how big each ask was.

    It deliberately does NOT bound its own request, so a test can see the size
    the caller handed down. A real implementation must (see `FileStore`); this
    one exists to observe the caller, not to model a good citizen."""

    def __init__(self) -> None:
        super().__init__()
        self.asks: list[int] = []

    async def read_many_existing(self, workspace_id: str, paths: list[str]) -> dict[str, bytes]:
        self.asks.append(len(paths))
        return {p: await self.read(workspace_id, p) for p in paths}


async def test_neither_lane_asks_for_everything_at_once() -> None:
    """A batch bounds the SIZE OF THE ASK, not just the number of asks.

    A workspace holds thousands of files, and handing all of them to one request
    is a different failure from the one batching fixes: `kb/graph/link.py` chunks
    at 500 because a 40,000-id predicate built a 937 KB statement the database
    refused outright. The cold lane's ask becomes one SQL `IN (...)`, so it needs
    the same bound the sandbox lane already has — one rule, both lanes."""
    store = _ChunkRecordingStore()
    files = WorkspaceFiles(store)  # cold: no sandbox
    paths = [f"/r/{i}.md" for i in range(450)]
    for path in paths:
        await files.write("ws1", path, b"x")

    got = await read_all(files, "ws1", paths)

    assert len(got) == 450
    assert store.asks and max(store.asks) <= WorkspaceFiles._BATCH_PATHS, (
        f"largest ask was {max(store.asks)} paths, over the {WorkspaceFiles._BATCH_PATHS} bound"
    )


async def test_one_read_resolves_liveness_once_however_many_paths() -> None:
    """A read is ONE operation, so it settles where the workspace lives once —
    at 450 paths exactly as at 3.

    Chunking in the free function called the facade once per chunk, so liveness
    was resolved per chunk. That is not just a cost: a sandbox reaped mid-read
    would answer the first chunk and the durable snapshot the rest, so one
    listing would be assembled from two different stores — the thing
    `_read_with` exists to make impossible."""
    sb = _BatchingSandbox()
    files = await _files(sb)
    for i in range(30, 450):
        await files.write("ws1", f"/r/{i}.md", f"body {i}".encode())
    sb.liveness_probes = 0

    await read_all(files, "ws1", [f"/r/{i}.md" for i in range(450)])

    assert sb.liveness_probes == 1, (
        f"{sb.liveness_probes} liveness resolutions for one read of 450 paths"
    )


async def test_the_whole_ask_goes_down_to_the_store_in_one_call() -> None:
    """`read_all` hands the store the WHOLE ask and lets it bound its own
    request — it does not chunk on the store's behalf.

    Chunking here called the store (or, through the facade, the facade) once per
    chunk, which is what made a single read resolve liveness repeatedly. Where
    the bound belongs is where the unbounded thing is: the SQL `IN` is built in
    `SpecstarFileStore`, and that is where it is capped
    (`tests/filestore/test_batch_read.py`)."""
    store = _ChunkRecordingStore()
    paths = [f"/r/{i}.md" for i in range(450)]
    for path in paths:
        await store.write("ws1", path, b"x")

    got = await read_all(store, "ws1", paths)  # the RAW store, no facade

    assert len(got) == 450
    assert store.asks == [450]


@pytest.mark.parametrize("tolerant", [False, True], ids=["strict", "tolerant"])
async def test_a_store_with_no_batch_at_all_is_read_one_path_at_a_time(tolerant: bool) -> None:
    """The floor. A store that never grew the capability — the wiki page store,
    a test double, anything but the workspace-backing one — is read per path and
    keeps both semantics: strict raises for what is gone, tolerant omits it.

    This is what "duck-typed optional capability" has to mean, and it is only
    true if somebody exercises it: the batch lanes are what production takes, so
    without this the fallback is a paragraph rather than a behaviour."""
    store = MemoryFileStore()  # no `read_many_existing`
    assert getattr(store, "read_many_existing", None) is None
    await store.write("ws1", "/r/0.md", b"body 0")
    await store.write("ws1", "/r/1.md", b"body 1")
    asked = ["/r/0.md", "/r/gone.md", "/r/1.md"]

    if tolerant:
        assert await read_all_existing(store, "ws1", asked) == {
            "/r/0.md": b"body 0",
            "/r/1.md": b"body 1",
        }
    else:
        with pytest.raises(FileNotFound):
            await read_all(store, "ws1", asked)
        assert await read_all(store, "ws1", ["/r/1.md", "/r/0.md"]) == [b"body 1", b"body 0"]


async def test_reading_nothing_costs_nothing() -> None:
    """An empty listing must not resolve liveness at all.

    `read_many` resolved it BEFORE looking at the paths, so an item with no
    skills and no sub-agents paid an extra probe — on every turn, since both
    indexes are rebuilt per message. The scaling tests could not see it: they
    assert "the cost for 2 equals the cost for 20", which stays true while the
    constant goes from one to two."""
    sb = _CountingSandbox()
    files = await _files(sb)
    sb.liveness_probes = 0

    assert await read_all(files, "ws1", []) == []
    assert await read_all_existing(files, "ws1", []) == {}

    assert sb.liveness_probes == 0, f"{sb.liveness_probes} liveness probes to read no files at all"


async def test_one_vanished_file_does_not_re_fetch_the_whole_listing() -> None:
    """The tolerant read must pay for the miss, not for the listing.

    Letting the batch raise and then re-reading every path singly turns one
    deleted file into a second full fetch — worse than the per-file loop this
    replaced, on the exact path a listing takes. The sandbox already says WHICH
    path was absent; throwing that away is what cost the re-read."""
    sb = _BatchingSandbox()
    files = await _files(sb)
    paths = [f"/r/{i}.md" for i in range(10)]
    sb.downloads = sb.batches = sb.liveness_probes = 0

    got = await read_all_existing(files, "ws1", [*paths, "/r/gone.md"])

    assert set(got) == set(paths)
    assert (sb.batches, sb.downloads) == (1, 0), (
        f"{sb.batches} batches + {sb.downloads} single downloads — the miss made "
        "it fetch everything again"
    )


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
