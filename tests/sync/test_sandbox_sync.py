import pytest

from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.monitor import InMemoryMonitor
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


async def test_restore_reports_per_file_progress(fs: SpecstarFileStore, sandbox: MockSandbox):
    """#492 P11: restore streams (done, total) so the FE can show '還原中 N/M'
    instead of a blank running card while a cold sandbox is restored. A leading
    0/total (so the card shows immediately, with the fraction known upfront) then
    one tick per restored file."""
    await fs.write("ws", "/a.txt", b"A")
    await fs.write("ws", "/sub/b.txt", b"BB")
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    seen: list[tuple[int, int]] = []
    await sync.restore("ws", h, on_progress=lambda done, total: seen.append((done, total)))
    # done ticks 0→2 regardless of ls() order; total is always the full count.
    assert seen == [(0, 2), (1, 2), (2, 2)]


async def test_restore_empty_workspace_emits_no_progress(
    fs: SpecstarFileStore, sandbox: MockSandbox
):
    """No files ⇒ instant wake ⇒ no progress frames (the FE never flashes a
    '還原中 0/0' card for an empty workspace)."""
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    seen: list[tuple[int, int]] = []
    n = await sync.restore("never", h, on_progress=lambda d, t: seen.append((d, t)))
    assert n == 0
    assert seen == []


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


async def test_mirror_is_not_quota_gated(fs: SpecstarFileStore, sandbox: MockSandbox):
    # #245 choice B: the mirror writes the durable store directly (not via the
    # quota-gated upload endpoint), so agent-created files are always persisted —
    # the quota guards user uploads, never destroys the agent's work.
    await fs.write("ws", "/already-big", b"x" * 50_000)  # workspace already large
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.upload(h, b"y" * 50_000, "/agent-made.bin")
    assert await sync.mirror("ws", h) == 1
    assert await fs.read("ws", "/agent-made.bin") == b"y" * 50_000


async def test_mirror_copies_large_files_into_snapshot(fs: SpecstarFileStore, sandbox: MockSandbox):
    # #374: a big agent-produced file (>10 MB) must be mirrored to the durable
    # snapshot too. The old MAX_FILE_SIZE cap silently dropped it, so it vanished
    # on sandbox reap and under-counted in the usage bar. write_from_path streams
    # to the blob store (#219), so size is not a durability concern.
    big = b"x" * (11 * 1024 * 1024)
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.upload(h, big, "/model.bin")
    assert await sync.mirror("ws", h) == 1
    assert await fs.read("ws", "/model.bin") == big


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


async def test_mirror_propagates_deletions_on_a_ready_sandbox(
    fs: SpecstarFileStore, sandbox: MockSandbox
):
    # #366: a genuine (shell `rm`) deletion on a COMPLETE sandbox still propagates
    # to the snapshot — readiness held throughout proves the missing file was
    # really removed, not merely a not-yet-restored state.
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.mark_ready(h)  # authoritative, complete sandbox
    await sandbox.upload(h, b"x", "/gone.txt")
    await sync.mirror("ws", h)
    assert await fs.exists("ws", "/gone.txt") is True

    await sandbox.delete(h, "/gone.txt")  # removed in the sandbox (shell rm)
    n = await sync.mirror("ws", h)
    assert n == 1  # one deletion
    assert await fs.exists("ws", "/gone.txt") is False


# ---- telemetry (#407): mirror/restore emit one summary event per call ----


async def test_mirror_records_a_telemetry_event(fs: SpecstarFileStore, sandbox: MockSandbox):
    mon = InMemoryMonitor()
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox, monitor=mon)
    await sandbox.upload(h, b"hello", "/a.txt")
    await sync.mirror("ws", h)
    events = [e for e in mon.recent() if e.get("kind") == "mirror"]
    assert len(events) == 1  # exactly one summary event per mirror call (not per file)
    ev = events[0]
    assert ev["group_id"] == "ws"
    assert ev["n_files"] == 1
    assert ev["n_uploaded"] == 1
    assert ev["n_deleted"] == 0
    assert ev["bytes"] == 5
    assert ev["elapsed_ms"] >= 0


async def test_restore_records_a_telemetry_event(fs: SpecstarFileStore, sandbox: MockSandbox):
    mon = InMemoryMonitor()
    await fs.write("ws", "/a.txt", b"A")
    await fs.write("ws", "/sub/b.txt", b"BB")
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox, monitor=mon)
    await sync.restore("ws", h)
    events = [e for e in mon.recent() if e.get("kind") == "restore"]
    assert len(events) == 1
    ev = events[0]
    assert ev["group_id"] == "ws"
    assert ev["n_files"] == 2
    assert ev["bytes"] == 3  # 1 + 2 bytes
    assert ev["elapsed_ms"] >= 0


async def test_mirror_event_counts_deletions(fs: SpecstarFileStore, sandbox: MockSandbox):
    mon = InMemoryMonitor()
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox, monitor=mon)
    await sandbox.mark_ready(h)
    await sandbox.upload(h, b"x", "/gone.txt")
    await sync.mirror("ws", h)
    await sandbox.delete(h, "/gone.txt")
    await sync.mirror("ws", h)
    ev = [e for e in mon.recent() if e.get("kind") == "mirror"][-1]  # the deletion mirror
    assert ev["n_files"] == 0  # workspace now empty
    assert ev["n_uploaded"] == 0
    assert ev["n_deleted"] == 1


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
    assert should_ignore(path, DEFAULT_IGNORES) is True


@pytest.mark.parametrize(
    "path",
    ["/src/main.py", "/README.md", "/data/x.json", "/.gitignore"],
)
def test_default_ignores_let_real_files_through(path: str):
    assert should_ignore(path, DEFAULT_IGNORES) is False


def test_large_files_are_not_ignored():
    # #374: no size cap — a big data file is a real file and must be backed up.
    # The size-based skip that used to drop it is gone.
    assert should_ignore("/totally_fine.bin", DEFAULT_IGNORES) is False


def test_ignore_literal_segment_pattern():
    """A pattern like 'secret' (no trailing /, no *.) matches a path
    segment with that exact name anywhere in the path."""
    assert should_ignore("/secret", ["secret"]) is True
    assert should_ignore("/sub/secret", ["secret"]) is True
    assert should_ignore("/not-a-secret", ["secret"]) is False


async def test_mirror_skips_ignored_paths(fs: SpecstarFileStore, sandbox: MockSandbox):
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.upload(h, b"x", "/src/main.py")
    await sandbox.upload(h, b"y", "/__pycache__/main.cpython-312.pyc")
    await sandbox.upload(h, b"z", "/.venv/bin/python")

    await sync.mirror("ws", h)
    assert await fs.ls("ws") == ["/src/main.py"]


# ---- #366 P3: the .ready sandwich (deletes only on a complete sandbox) ----


async def test_mirror_does_not_delete_snapshot_when_sandbox_not_ready_366(
    fs: SpecstarFileStore, sandbox: MockSandbox
):
    # #366 face B: a (re)created sandbox that is not yet ready — mid rebuild,
    # before restore completes — must NOT let the deletion-aware mirror wipe the
    # durable snapshot (that is what emptied the filetree).
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.mark_ready(h)  # a complete, authoritative sandbox
    await sandbox.upload(h, b"data", "/keep.txt")
    await sync.mirror("ws", h)  # ready → keep.txt backed up + tracked
    assert await fs.exists("ws", "/keep.txt") is True

    # the item is served by a freshly (re)created sandbox — EMPTY and not yet
    # ready (its restore is still in flight); same workspace id, so the mirror's
    # diff state still remembers keep.txt.
    rebuilt = await sandbox.create(SandboxSpec())
    await sync.mirror("ws", rebuilt)  # not ready → deletion phase skipped
    assert await fs.exists("ws", "/keep.txt") is True  # snapshot preserved


async def test_restore_marks_sandbox_ready_366(fs: SpecstarFileStore, sandbox: MockSandbox):
    await fs.write("ws", "/a.txt", b"A")
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    assert await sandbox.is_ready(h) is False  # not yet authoritative
    await sync.restore("ws", h)
    assert await sandbox.is_ready(h) is True  # marked authoritative
    # readiness is out of the workspace, so restore never tracks it as a file
    assert "/.ready" not in {e.path for e in await sandbox.walk(h, "/")}
    # a second restore is idempotent
    await sync.restore("ws", h)
    assert await sandbox.is_ready(h) is True


async def test_mirror_on_reaped_sandbox_is_a_noop_366(fs: SpecstarFileStore, sandbox: MockSandbox):
    # #366: the sandbox was reaped (dir gone) — walk raises SandboxNotFound. The
    # mirror skips cleanly (no crash, no deletion) rather than wiping the snapshot.
    await fs.write("ws", "/keep.txt", b"data")
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(filestore=fs, sandbox=sandbox)
    await sandbox.kill(h)  # host reaped it
    assert await sync.mirror("ws", h) == 0
    assert await fs.exists("ws", "/keep.txt") is True


async def test_mirror_skips_deletion_when_readiness_drops_mid_walk_366(
    fs: SpecstarFileStore,
):
    class _ReadinessDropsMidWalk(MockSandbox):
        def __init__(self) -> None:
            super().__init__()
            self.arm = False

        async def walk(self, handle, root):  # type: ignore[override]
            if self.arm and await self.is_ready(handle):
                # teardown drops readiness FIRST, then starts removing files
                self._ready.discard(handle.id)
                await self.delete(handle, "/keep.txt")
                self.arm = False
            return await super().walk(handle, root)

    sb = _ReadinessDropsMidWalk()
    h = await sb.create(SandboxSpec())
    await sb.mark_ready(h)
    await sb.upload(h, b"data", "/keep.txt")
    sync = SandboxSync(filestore=fs, sandbox=sb)
    await sync.mirror("ws", h)  # calm → keep.txt tracked + backed up
    assert await fs.exists("ws", "/keep.txt") is True

    sb.arm = True
    await sync.mirror("ws", h)  # readiness drops during walk → gate 2 skips deletes
    assert await fs.exists("ws", "/keep.txt") is True


async def test_mirror_skips_deletion_when_sandbox_vanishes_mid_walk_366(
    fs: SpecstarFileStore,
):
    class _SandboxVanishesMidWalk(MockSandbox):
        def __init__(self) -> None:
            super().__init__()
            self.arm = False

        async def walk(self, handle, root):  # type: ignore[override]
            entries = await super().walk(handle, root)
            if self.arm:
                await self.kill(handle)  # whole sandbox gone right after we read it
                self.arm = False
            return entries

    sb = _SandboxVanishesMidWalk()
    h = await sb.create(SandboxSpec())
    await sb.mark_ready(h)
    await sb.upload(h, b"data", "/keep.txt")
    sync = SandboxSync(filestore=fs, sandbox=sb)
    await sync.mirror("ws", h)
    assert await fs.exists("ws", "/keep.txt") is True

    sb.arm = True
    await sync.mirror("ws", h)  # sandbox vanishes mid-walk → gate 2 re-check raises → skip
    assert await fs.exists("ws", "/keep.txt") is True


async def test_mirror_hands_the_quota_the_sizes_it_just_walked(
    fs: SpecstarFileStore, sandbox: MockSandbox
):
    # #538 follow-up: the sweep already walks the whole workspace every few
    # seconds, so the quota takes its measurement from here instead of walking
    # again on somebody's request. The set it reports is the one it PERSISTS —
    # post-`should_ignore` — so the quota counts exactly the bytes that reach
    # the durable store, and can't be filled up by regenerable build junk.
    measured: dict[str, dict[str, int]] = {}
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(
        filestore=fs, sandbox=sandbox, on_measured=lambda ws, sizes: measured.update({ws: sizes})
    )
    await sandbox.upload(h, b"x" * 10, "/keep.txt")
    await sandbox.upload(h, b"y" * 5000, "/node_modules/dep/index.js")
    await sandbox.mark_ready(h)

    await sync.mirror("ws", h)

    assert measured["ws"] == {"/keep.txt": 10}


async def test_mirror_publishes_no_measurement_from_a_half_restored_sandbox(
    fs: SpecstarFileStore, sandbox: MockSandbox
):
    # A sandbox that isn't ready is mid-restore: its file set is partial, so
    # measuring it would under-report and let writes through that shouldn't be.
    measured: dict[str, dict[str, int]] = {}
    h = await sandbox.create(SandboxSpec())
    sync = SandboxSync(
        filestore=fs, sandbox=sandbox, on_measured=lambda ws, sizes: measured.update({ws: sizes})
    )
    await sandbox.upload(h, b"x" * 10, "/partial.txt")

    await sync.mirror("ws", h)  # never marked ready

    assert measured == {}
