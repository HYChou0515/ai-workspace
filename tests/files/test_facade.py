"""WorkspaceFiles facade — P1: a single chokepoint for workspace file ops that
delegates to FileStore (behaviour unchanged). P2 flips its internals to route
by sandbox liveness; callers don't change."""

import pytest

from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.protocol import FileNotFound
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxBusy, SandboxHandle, SandboxSpec

WS = "inv-1"


def _resolver(fn):
    """Adapt a sync ``item → handle`` function into the async resolver the facade
    now expects (#492: resolution is global/async so it can consult the shared
    address store). Reads ``fn`` at call time so tests can mutate the handle."""

    async def _resolve(ws):
        return fn(ws)

    return _resolve


async def _files() -> WorkspaceFiles:
    return WorkspaceFiles(MemoryFileStore())


async def test_write_read_roundtrip():
    f = await _files()
    await f.write(WS, "/a.txt", b"hello")
    assert await f.read(WS, "/a.txt") == b"hello"
    assert await f.exists(WS, "/a.txt") is True
    assert await f.exists(WS, "/missing") is False


async def test_read_missing_raises():
    f = await _files()
    with pytest.raises(FileNotFound):
        await f.read(WS, "/nope")


async def test_ls_and_delete():
    f = await _files()
    await f.write(WS, "/a.txt", b"x")
    await f.write(WS, "/sub/b.txt", b"y")
    assert sorted(await f.ls(WS)) == ["/a.txt", "/sub/b.txt"]
    assert await f.ls(WS, "/sub") == ["/sub/b.txt"]
    await f.delete(WS, "/a.txt")
    assert await f.exists(WS, "/a.txt") is False


async def test_dirs_mkdir_listdir_is_dir_rmdir():
    f = await _files()
    await f.mkdir(WS, "/d/e")
    assert await f.is_dir(WS, "/d/e") is True
    assert "/d/e" in await f.listdir(WS)
    await f.rmdir(WS, "/d")
    assert await f.is_dir(WS, "/d") is False


# ---------------- P2: liveness routing (warm→sandbox, cold→snapshot) ----------------


async def _wired() -> tuple[WorkspaceFiles, MemoryFileStore, MockSandbox, dict]:
    fs = MemoryFileStore()
    sb = MockSandbox()
    handle = {"h": None}  # mutate to simulate wake/idle-kill
    files = WorkspaceFiles(fs, sandbox=sb, handle_for=_resolver(lambda _ws: handle["h"]))
    return files, fs, sb, handle


async def test_cold_ops_hit_snapshot():
    files, fs, _sb, _h = await _wired()
    await files.write(WS, "/a.txt", b"cold")
    assert await fs.read(WS, "/a.txt") == b"cold"  # went to snapshot
    assert await files.read(WS, "/a.txt") == b"cold"
    assert await files.exists(WS, "/a.txt") is True
    assert await files.ls(WS) == ["/a.txt"]


async def test_derivable_handle_for_cold_sandbox_falls_back_to_snapshot_345():
    # #345: resolve_io_handle hands back a derivable (id-based) handle even when the
    # item's shared dir is cold (e.g. a read on a pod that never woke it). The
    # facade probes, hits SandboxNotFound, and serves the durable snapshot
    # instead of erroring — so a cross-pod read stays consistent.
    fs = MemoryFileStore()
    sb = MockSandbox()
    await fs.write(WS, "/archived.txt", b"from snapshot")
    files = WorkspaceFiles(fs, sandbox=sb, handle_for=_resolver(lambda ws: SandboxHandle(id=ws)))
    assert await files.read(WS, "/archived.txt") == b"from snapshot"
    assert await files.ls(WS) == ["/archived.txt"]
    assert await files.exists(WS, "/archived.txt") is True


async def test_warm_ops_hit_sandbox_not_snapshot():
    files, fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    await files.write(WS, "/b.txt", b"warm")
    # written to the sandbox, NOT the snapshot
    assert await sb.download(handle["h"], "/b.txt") == b"warm"
    assert await fs.exists(WS, "/b.txt") is False
    assert await files.read(WS, "/b.txt") == b"warm"
    assert await files.exists(WS, "/b.txt") is True
    assert await files.ls(WS) == ["/b.txt"]
    await files.delete(WS, "/b.txt")
    assert await files.exists(WS, "/b.txt") is False


async def test_write_from_path_cold_hits_snapshot(tmp_path):
    files, fs, _sb, _h = await _wired()
    src = tmp_path / "src.bin"
    src.write_bytes(b"streamed-cold")
    await files.write_from_path(WS, "/up/a.bin", src, "application/octet-stream")
    assert await fs.read(WS, "/up/a.bin") == b"streamed-cold"  # snapshot, not sandbox
    assert await files.read(WS, "/up/a.bin") == b"streamed-cold"


async def test_write_from_path_warm_hits_sandbox_not_snapshot(tmp_path):
    files, fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    src = tmp_path / "src.bin"
    src.write_bytes(b"streamed-warm")
    await files.write_from_path(WS, "/up/b.bin", src)
    assert await sb.download(handle["h"], "/up/b.bin") == b"streamed-warm"
    assert await fs.exists(WS, "/up/b.bin") is False  # durability waits for mirror
    assert await files.read(WS, "/up/b.bin") == b"streamed-warm"


async def test_read_to_file_cold_streams_from_snapshot(tmp_path):
    files, _fs, _sb, _h = await _wired()
    await files.write(WS, "/a.bin", b"cold-out")
    dest = tmp_path / "out.bin"
    await files.read_to_file(WS, "/a.bin", dest)
    assert dest.read_bytes() == b"cold-out"


async def test_read_to_file_warm_streams_from_sandbox(tmp_path):
    files, _fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    await files.write(WS, "/b.bin", b"warm-out")
    dest = tmp_path / "out.bin"
    await files.read_to_file(WS, "/b.bin", dest)
    assert dest.read_bytes() == b"warm-out"


async def test_read_to_file_warm_missing_maps_to_filenotfound(tmp_path):
    files, _fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    with pytest.raises(FileNotFound):
        await files.read_to_file(WS, "/nope", tmp_path / "out.bin")


async def test_warm_read_missing_maps_to_filenotfound():
    files, _fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    with pytest.raises(FileNotFound):
        await files.read(WS, "/nope")
    with pytest.raises(FileNotFound):
        await files.delete(WS, "/nope")


async def test_warm_is_dir_and_listdir_derived_from_walk():
    files, _fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    await files.write(WS, "/d/sub/f.txt", b"x")
    assert await files.is_dir(WS, "/d") is True
    assert await files.is_dir(WS, "/d/sub") is True
    assert await files.is_dir(WS, "/d/sub/f.txt") is False
    assert set(await files.listdir(WS)) == {"/d", "/d/sub"}


# ---------------- P4: compare-and-swap writes ----------------


async def test_create_is_create_only():
    f = await _files()
    assert await f.create(WS, "/a.txt", b"first") is None  # created
    # second create conflicts and returns the current bytes (no clobber)
    assert await f.create(WS, "/a.txt", b"second") == b"first"
    assert await f.read(WS, "/a.txt") == b"first"


async def test_edit_replaces_unique_match():
    f = await _files()
    await f.write(WS, "/a.txt", b"hello world")
    assert await f.edit(WS, "/a.txt", "world", "there") is None
    assert await f.read(WS, "/a.txt") == b"hello there"


async def test_edit_conflict_returns_current_when_text_absent_or_ambiguous():
    f = await _files()
    await f.write(WS, "/a.txt", b"a a")
    # ambiguous (two matches) → conflict, returns current text unchanged
    assert await f.edit(WS, "/a.txt", "a", "b") == "a a"
    assert await f.read(WS, "/a.txt") == b"a a"
    # absent text → conflict
    assert await f.edit(WS, "/a.txt", "zzz", "b") == "a a"
    # missing file → conflict with empty base
    assert await f.edit(WS, "/missing", "x", "y") == ""


async def test_warm_mkdir_and_rmdir():
    files, fs, sb, handle = await _wired()
    handle["h"] = await sb.create(SandboxSpec())
    await files.mkdir(WS, "/d")  # no-op on the flat mock store, but routed warm
    await files.write(WS, "/d/f.txt", b"x")
    await files.rmdir(WS, "/d")  # routed to sandbox; removes the subtree
    assert await files.exists(WS, "/d/f.txt") is False
    assert await fs.exists(WS, "/d/f.txt") is False  # never touched the snapshot
    with pytest.raises(FileNotFound):
        await files.rmdir(WS, "/d")  # gone → FileNotFoundError mapped to FileNotFound


# ─── stat_all: batch (path, size) WITHOUT reading file bytes (#362) ─────


class _CountingSandbox(MockSandbox):
    """A MockSandbox that counts full-content `download` calls, so a test can
    assert that listing a file tree never reads any file's bytes (#362)."""

    def __init__(self) -> None:
        super().__init__()
        self.download_calls = 0

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        self.download_calls += 1
        return await super().download(handle, remote_path)


async def test_stat_all_warm_reports_sizes_from_walk_without_reading_bytes():
    """#362: warm listing takes sizes straight from `walk` (which stats, never
    reads) — so building a 600-file tree does zero full-content downloads."""
    fs = MemoryFileStore()
    sb = _CountingSandbox()
    handle: dict[str, SandboxHandle | None] = {"h": None}
    files = WorkspaceFiles(fs, sandbox=sb, handle_for=_resolver(lambda _ws: handle["h"]))
    handle["h"] = await sb.create(SandboxSpec())
    await files.write(WS, "/a.txt", b"hello")  # 5 bytes
    await files.write(WS, "/sub/b.txt", b"world!")  # 6 bytes
    entries = await files.stat_all(WS)
    assert sorted(entries) == [("/a.txt", 5), ("/sub/b.txt", 6)]
    assert sb.download_calls == 0  # never read any file's content


async def test_stat_all_warm_honours_prefix():
    fs = MemoryFileStore()
    sb = _CountingSandbox()
    handle: dict[str, SandboxHandle | None] = {"h": None}
    files = WorkspaceFiles(fs, sandbox=sb, handle_for=_resolver(lambda _ws: handle["h"]))
    handle["h"] = await sb.create(SandboxSpec())
    await files.write(WS, "/a.txt", b"x")
    await files.write(WS, "/sub/b.txt", b"yy")
    assert await files.stat_all(WS, "/sub") == [("/sub/b.txt", 2)]
    assert sb.download_calls == 0


async def test_stat_all_cold_uses_store_batch():
    """Cold (no live sandbox): sizes come from the durable store's own batch
    ``stat_all`` — the snapshot metadata, no blob reads."""
    files, fs, _sb, _h = await _wired()  # handle is None → cold
    await files.write(WS, "/a.txt", b"hello")
    await files.write(WS, "/sub/b.txt", b"world!")
    assert sorted(await files.stat_all(WS)) == [("/a.txt", 5), ("/sub/b.txt", 6)]


async def test_stat_all_cold_falls_back_to_zero_size_when_store_lacks_batch():
    """An exotic store without a batch ``stat_all`` still lists — paths with an
    unknown size of 0, and never a blob read (just ``ls``)."""

    class _NoStatStore:
        async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
            return ["/x.txt", "/y.txt"]

    files = WorkspaceFiles(_NoStatStore())  # ty: ignore[invalid-argument-type]
    assert sorted(await files.stat_all(WS)) == [("/x.txt", 0), ("/y.txt", 0)]


# ─── path normalization (./, /, bare all map to same key) ──────────────


async def test_paths_with_with_and_without_leading_dot_slash_target_same_file():
    """`./brief.md`, `/brief.md`, `brief.md` all resolve to the same
    file. Lets prompts use `./` (shell-conventional) while the underlying
    store stays canonical."""
    from workspace_app.files.facade import WorkspaceFiles
    from workspace_app.filestore.memory import MemoryFileStore

    fs = WorkspaceFiles(MemoryFileStore())
    await fs.write("inv-1", "./brief.md", b"hello")
    # Reads with the other two forms see the same content.
    assert await fs.read("inv-1", "/brief.md") == b"hello"
    assert await fs.read("inv-1", "brief.md") == b"hello"
    # exists agrees too.
    assert await fs.exists("inv-1", "./brief.md")
    assert await fs.exists("inv-1", "/brief.md")
    assert await fs.exists("inv-1", "brief.md")


async def test_normalize_helper_canonicalises_all_three_forms():
    """Direct unit test on the helper — guards against subtle regressions
    (e.g. someone using `lstrip('./')` which would also strip '.')."""
    from workspace_app.files.facade import _norm

    assert _norm("./brief.md") == "/brief.md"
    assert _norm("/brief.md") == "/brief.md"
    assert _norm("brief.md") == "/brief.md"
    # Subdirs survive.
    assert _norm("./data/x.csv") == "/data/x.csv"
    # A bare `.brief.md` (no slash) keeps the leading dot — only `./` strips.
    assert _norm(".brief.md") == "/.brief.md"


# ---------------- #492: host-managed durable — same-source, never cold-write ----------------


async def test_warm_write_rebuilds_a_reaped_sandbox_instead_of_cold_writing_492():
    """#492 core: on a host-managed (http) backend, an item that is globally warm
    but whose resolved handle was reaped must REBUILD and write into the fresh
    live sandbox — NOT fall back to a cold durable write the host's `--delete`
    mirror would reconcile away. The `rebuild` callback is what makes this an
    http-only behaviour (local shared-vol has rebuild=None → cold-dir→durable)."""
    fs = MemoryFileStore()
    sb = MockSandbox()
    dead = SandboxHandle(id="reaped")  # never created ⇒ exists raises SandboxNotFound
    live = await sb.create(SandboxSpec())
    rebuilt = {"n": 0}

    async def _resolve(_ws):
        return dead  # the address still points at the reaped handle

    async def _rebuild(_ws):
        rebuilt["n"] += 1
        return live  # ...but rebuild yields a fresh live sandbox

    files = WorkspaceFiles(fs, sandbox=sb, handle_for=_resolve, rebuild=_rebuild)
    await files.write(WS, "/x.txt", b"warm")

    assert rebuilt["n"] == 1  # reaped-but-warm ⇒ rebuilt, not cold
    assert await sb.download(live, "/x.txt") == b"warm"  # landed in the LIVE sandbox
    assert await fs.exists(WS, "/x.txt") is False  # NOT a cold durable write
    # And a subsequent read routes to the same rebuilt sandbox (same source).
    assert await files.read(WS, "/x.txt") == b"warm"


async def test_warm_op_propagates_busy_and_never_cold_writes_or_rebuilds_492():
    """#492: a BUSY host (SandboxBusy — reachable but slow, already retried by the
    http client) must fail loud, NOT cold-write (the item is warm, a cold write is
    lost to `--delete`) and NOT rebuild (the sandbox is alive → split-brain)."""

    class _BusyOnProbe(MockSandbox):
        async def exists(self, handle: SandboxHandle, path: str) -> bool:
            raise SandboxBusy(handle.id)

    fs = MemoryFileStore()
    sb = _BusyOnProbe()
    h = await sb.create(SandboxSpec())
    rebuilt = {"n": 0}

    async def _resolve(_ws):
        return h

    async def _rebuild(_ws):  # pragma: no cover — must NOT be called for a busy host
        rebuilt["n"] += 1
        return h

    files = WorkspaceFiles(fs, sandbox=sb, handle_for=_resolve, rebuild=_rebuild)
    with pytest.raises(SandboxBusy):
        await files.write(WS, "/x.txt", b"warm")

    assert rebuilt["n"] == 0  # never rebuilt a live (busy) sandbox
    assert await fs.exists(WS, "/x.txt") is False  # never cold-wrote
