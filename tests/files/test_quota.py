"""Quota accounting through the WorkspaceFiles facade (#245). `remaining_quota`
is the per-write headroom the upload/edit endpoints gate on — an overwrite is a
*replace* (delta), and a disabled quota (0) returns None.

#538: the measurement follows the SAME warm/cold routing every other facade op
uses — a warm workspace is measured from the live sandbox, so bytes the agent
created there (exec output, downloads) count and bytes it deleted stop counting.
"""

import asyncio

import pytest

from workspace_app.files.facade import WorkspaceFiles, WorkspaceFull
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.protocol import FileExists
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec


def _files() -> WorkspaceFiles:
    return WorkspaceFiles(MemoryFileStore())


class _WalkCountingSandbox(MockSandbox):
    """Counts `walk` calls and root liveness probes, so a test can assert the
    quota re-measures on a window boundary rather than on every question asked
    of it, and that gating a write doesn't double the probing it costs."""

    def __init__(self) -> None:
        super().__init__()
        self.walk_calls = 0
        self.liveness_probes = 0

    async def walk(self, handle: SandboxHandle, root: str):  # type: ignore[no-untyped-def]
        self.walk_calls += 1
        return await super().walk(handle, root)

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        if path == "/":  # WorkspaceFiles._warm's liveness probe
            self.liveness_probes += 1
        return await super().exists(handle, path)


async def test_gating_a_write_costs_one_liveness_probe_not_two():
    # The gate has to know whether the workspace is warm, and so does the write
    # that follows it. Resolving that twice would put an extra sandbox
    # round-trip on every single write — invisible on the mock, a real cost
    # against the hosted (http) sandbox.
    fs = MemoryFileStore()
    sb = _WalkCountingSandbox()
    handle = await sb.create(SandboxSpec())

    async def _resolve(_ws: str) -> SandboxHandle:
        return handle

    files = WorkspaceFiles(fs, sandbox=sb, handle_for=_resolve, quota=1000)
    sb.liveness_probes = 0
    await files.write("ws1", "/a", b"x" * 10)
    assert sb.liveness_probes == 1


async def test_usage_is_remeasured_once_per_window_not_once_per_question():
    # Measuring the live workspace means walking it, and a folder upload asks the
    # quota once per file — walking each time would make an N-file batch cost N
    # traversals. One measurement per mirror window is what the durable snapshot
    # was really buying us, without the snapshot's staleness bugs.
    fs = MemoryFileStore()
    sb = _WalkCountingSandbox()
    handle = await sb.create(SandboxSpec())
    clock = {"t": 1000.0}

    async def _resolve(_ws: str) -> SandboxHandle:
        return handle

    files = WorkspaceFiles(
        fs, sandbox=sb, handle_for=_resolve, usage_window=5.0, now=lambda: clock["t"]
    )
    await sb.upload(handle, b"z" * 400, "/generated.bin")

    assert await files.workspace_usage("ws1") == 400
    walks = sb.walk_calls
    assert await files.workspace_usage("ws1") == 400  # same window → memoised
    assert sb.walk_calls == walks

    await sb.upload(handle, b"z" * 100, "/more.bin")
    assert await files.workspace_usage("ws1") == 400  # still the window's answer
    clock["t"] += 5.0
    assert await files.workspace_usage("ws1") == 500  # window elapsed → re-measured


async def _warm(
    quota: int = 0,
) -> tuple[WorkspaceFiles, MemoryFileStore, MockSandbox, SandboxHandle]:
    """A facade whose workspace has a live sandbox — the state a real item is in
    while the agent works, and the one the durable-snapshot measurement got wrong."""
    fs = MemoryFileStore()
    sb = MockSandbox()
    handle = await sb.create(SandboxSpec())

    async def _resolve(_ws: str) -> SandboxHandle:
        return handle

    return WorkspaceFiles(fs, sandbox=sb, handle_for=_resolve, quota=quota), fs, sb, handle


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


async def test_headroom_bottoms_out_at_the_file_s_current_size_when_already_over():
    # #538: this used to report negative headroom, which made the upload endpoint
    # refuse EVERY write to an over-quota workspace — including the shrinks the
    # user was being told to perform. The floor is the path's current size, so a
    # replace that doesn't grow the workspace always fits.
    files = _files()
    await files.write("ws1", "/a", b"x" * 1500)
    assert await files.remaining_quota("ws1", "/b", quota=1000) == 0  # a new file: nothing fits
    assert await files.remaining_quota("ws1", "/a", quota=1000) == 1500  # but /a may stay /a's size


async def test_warm_usage_counts_bytes_the_agent_created_in_the_sandbox():
    # #538 (2): a file the agent produced inside the sandbox (exec output, a
    # download) exists only there until a mirror sweep. Measuring the durable
    # snapshot reported 0 for it, so AI-generated bytes were free.
    files, fs, sb, handle = await _warm()
    await sb.upload(handle, b"z" * 400, "/generated.bin")
    assert await fs.workspace_usage("ws1") == 0  # not mirrored yet
    assert await files.workspace_usage("ws1") == 400


async def test_warm_overwrite_credits_the_size_the_sandbox_actually_holds():
    # The replace-credit has to come from the same source as `used`, or the two
    # halves of the subtraction disagree: warm-only bytes counted against the
    # workspace but credited back as 0, so re-uploading a file over itself ate
    # its own size twice.
    files, _fs, _sb, _handle = await _warm()
    await files.write("ws1", "/a", b"x" * 300)  # lands in the sandbox only
    assert await files.remaining_quota("ws1", "/a", quota=1000) == 1000


async def test_deleting_in_the_sandbox_frees_headroom_immediately():
    # #538 (1): the symptom users hit — clear out the workspace, still be told
    # "out of space". The durable snapshot kept charging for files the sandbox
    # no longer had until a mirror sweep reconciled the deletion.
    files, _fs, _sb, _handle = await _warm()
    await files.write("ws1", "/big", b"x" * 900)
    assert await files.remaining_quota("ws1", "/new", quota=1000) == 100
    await files.delete("ws1", "/big")
    assert await files.remaining_quota("ws1", "/new", quota=1000) == 1000


async def test_a_write_past_the_quota_is_refused_and_lands_nothing():
    # #538 (3): the quota was enforced only by the upload endpoint, so everything
    # that wasn't a user upload — the agent's own write_file, a workflow, the IDE
    # save — sailed straight past it. The facade is the one chokepoint they all
    # share, so the rule belongs here.
    files = WorkspaceFiles(MemoryFileStore(), quota=1000)
    await files.write("ws1", "/a", b"x" * 900)
    with pytest.raises(WorkspaceFull) as caught:
        await files.write("ws1", "/b", b"y" * 200)
    assert caught.value.used == 900
    assert caught.value.quota == 1000
    assert await files.exists("ws1", "/b") is False


async def test_an_over_quota_workspace_can_still_be_tidied_up():
    # A workspace CAN end up over quota — the mirror writes the durable store
    # directly and stays ungated so agent work is never lost. If the gate keyed
    # on "already over" rather than "would grow", such a workspace would be
    # wedged: we'd tell the user to delete things while refusing the very writes
    # that shrink it. Shrinking, same-size replacement and deletes stay open.
    store = MemoryFileStore()
    await store.write("ws1", "/huge", b"x" * 2000)  # as the ungated mirror would
    files = WorkspaceFiles(store, quota=1000)

    await files.write("ws1", "/huge", b"x" * 1500)  # shrink: allowed
    await files.write("ws1", "/huge", b"y" * 1500)  # same size: allowed
    with pytest.raises(WorkspaceFull):
        await files.write("ws1", "/huge", b"x" * 1600)  # growth: still refused
    await files.delete("ws1", "/huge")
    assert await files.workspace_usage("ws1") == 0


async def test_regenerable_build_junk_is_not_charged_to_the_quota():
    # The mirror filters `should_ignore` paths out before writing the durable
    # store, so those bytes never reach the disk this quota protects. Charging
    # for them would let one `npm install` inside the workspace consume a 20 GiB
    # quota with content that is never persisted — and would make the number
    # drop discontinuously when the sandbox is reaped and the measurement falls
    # back to the durable side. (`registry._scratch_usage` counting them IS
    # right: that cap guards the scratch volume, which really does hold them.)
    files, _fs, sb, handle = await _warm()
    await sb.upload(handle, b"x" * 100, "/keep.bin")
    await sb.upload(handle, b"y" * 5000, "/node_modules/left-pad/index.js")
    await sb.upload(handle, b"z" * 5000, "/.venv/lib/thing.py")
    await sb.upload(handle, b"w" * 5000, "/src/__pycache__/mod.cpython-312.pyc")
    assert await files.workspace_usage("ws1") == 100


async def test_warm_file_size_reads_the_sandbox_not_the_snapshot():
    files, fs, sb, handle = await _warm()
    await sb.upload(handle, b"z" * 250, "/only-in-sandbox.bin")
    assert await fs.file_size("ws1", "/only-in-sandbox.bin") is None  # not mirrored
    assert await files.file_size("ws1", "/only-in-sandbox.bin") == 250
    assert await files.file_size("ws1", "/nope.bin") is None


async def test_create_exclusive_is_gated_and_counted():
    files, _fs, _sb, _handle = await _warm()
    await files.create_exclusive("ws1", "/claim", b"x" * 40)
    # counted immediately, without waiting for a re-walk
    assert await files.workspace_usage("ws1") == 40


async def test_measuring_one_workspace_keeps_another_freshly_measured_one():
    # The expiry sweep rides along with a walk, so it must not throw away
    # measurements that are still inside their window — that would turn one
    # workspace's re-walk into a re-walk for every other workspace too.
    fs = MemoryFileStore()
    sb = _WalkCountingSandbox()
    handle = await sb.create(SandboxSpec())
    clock = {"t": 0.0}

    async def _resolve(_ws: str) -> SandboxHandle:
        return handle

    files = WorkspaceFiles(
        fs, sandbox=sb, handle_for=_resolve, usage_window=5.0, now=lambda: clock["t"]
    )
    await files.workspace_usage("ws1")
    clock["t"] = 6.0  # ws1's measurement expires
    await files.workspace_usage("ws2")
    clock["t"] = 7.0  # ws1 re-measures; ws2 is only 1s old and must survive
    await files.workspace_usage("ws1")
    walks = sb.walk_calls
    await files.workspace_usage("ws2")
    assert sb.walk_calls == walks  # ws2 answered from its surviving measurement


async def test_create_exclusive_reports_a_taken_name_even_when_full():
    # `FileExists` is an answer callers act on — the entity numbering walk moves
    # to the next free number on it. Reporting "full" for a name that was taken
    # anyway would abort a search that had nothing to do with space.
    files = WorkspaceFiles(MemoryFileStore(), quota=1000)
    await files.create_exclusive("ws1", "/claim", b"x" * 990)
    with pytest.raises(FileExists):
        await files.create_exclusive("ws1", "/claim", b"y" * 500)


async def test_create_exclusive_warm_reports_a_taken_name_even_when_full():
    files, _fs, _sb, _handle = await _warm(quota=1000)
    await files.create_exclusive("ws1", "/claim", b"x" * 990)
    with pytest.raises(FileExists):
        await files.create_exclusive("ws1", "/claim", b"y" * 500)


async def test_room_for_nothing_is_always_available():
    # A copy of an empty subtree, and any facade with no quota, must not be
    # refused — and must not pay for a measurement to find that out.
    files = WorkspaceFiles(MemoryFileStore(), quota=100)
    await files.write("ws1", "/a", b"x" * 100)
    await files.ensure_room_for("ws1", 0)
    with pytest.raises(WorkspaceFull):
        await files.ensure_room_for("ws1", 1)
    unlimited = WorkspaceFiles(MemoryFileStore())
    await unlimited.ensure_room_for("ws1", 10**9)


async def test_two_racing_measurements_walk_once_and_do_not_lose_a_write():
    # Both coroutines miss the memo and both would walk; whichever finished LAST
    # installed its map, discarding any write recorded against the other one. The
    # workspace then under-counted for the rest of the window — by however many
    # writes raced, not by a bounded amount.
    fs = MemoryFileStore()
    sb = _WalkCountingSandbox()
    handle = await sb.create(SandboxSpec())
    gate = asyncio.Event()

    inner_walk = sb.walk

    async def _slow_walk(h: SandboxHandle, root: str):
        await gate.wait()
        return await inner_walk(h, root)

    sb.walk = _slow_walk  # ty: ignore[invalid-assignment]

    async def _resolve(_ws: str) -> SandboxHandle:
        return handle

    files = WorkspaceFiles(fs, sandbox=sb, handle_for=_resolve)
    await sb.upload(handle, b"x" * 100, "/seed.bin")

    racers = [asyncio.create_task(files.workspace_usage("ws1")) for _ in range(2)]
    await asyncio.sleep(0)
    gate.set()
    assert await asyncio.gather(*racers) == [100, 100]
    assert sb.walk_calls == 1  # the second took the first's measurement

    await files.write("ws1", "/added.bin", b"y" * 50)
    assert await files.workspace_usage("ws1") == 150  # the write survived


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
