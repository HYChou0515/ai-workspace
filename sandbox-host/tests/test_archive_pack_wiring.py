"""The host half of the pack decision (docs/plan-archive-pack.md).

A reap's write-back asks for a pack (`persist {pack: true}`); the host makes it
while it TEARS THE SANDBOX DOWN, with the sandbox already closed to every other
request. Packing inside the write-back instead could not work: every app pod
checkpoints every warm sandbox every few seconds, idle ones included, so a pack
long enough to matter was always interrupted — and nothing a guard could do
would stop the next checkpoint from arriving.

Closing first is what makes "nobody writes the dir during the tar" true rather
than hoped for: a request that is already running (an `exec` still streaming)
is seen by the in-flight count, and one that arrives later is turned away.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.mock import MockSandbox
from sandbox_host.protocol import ExecResult, SandboxHandle


class _Archive:
    """Records persists and packs; `during` runs inside the pack, standing in
    for the tar, so a test can put a request exactly there."""

    def __init__(self, *, reconciles: bool = True, packs: bool = True) -> None:
        self.reconciles = reconciles
        self.packs_enabled = packs
        self.persists: list[bool] = []
        self.packs: list[str] = []
        self.during = None
        self.fails = False
        # A checkpoint (`delete` off) waits here when set — one still running.
        self.checkpoint_gate: asyncio.Event | None = None
        self.checkpoint_entered = asyncio.Event()

    @property
    def packing(self) -> bool:
        return self.packs_enabled

    async def restore(self, item_id: str, workspace_dir: Path) -> bool:
        return False

    async def persist(self, item_id, workspace_dir, *, delete) -> bool:
        self.persists.append(delete)
        if not delete and self.checkpoint_gate is not None:
            self.checkpoint_entered.set()
            await self.checkpoint_gate.wait()
        return delete and self.reconciles

    async def pack(self, item_id, workspace_dir) -> bool:
        if self.during is not None:
            await self.during()
        if self.fails:
            raise OSError("NFS went away")
        self.packs.append(item_id)
        return True


class _BlockingExec(MockSandbox):
    """An exec that stays open until released — the shape of a long command."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def exec(self, handle: SandboxHandle, cmd, on_output=None, env=None, exec_timeout=None):
        self.entered.set()
        await self.release.wait()
        return ExecResult(exit_code=0, stdout=b"", stderr=b"")


# Short, so a teardown that waits on the wrong count fails a test in a second
# instead of hanging the suite for the production default.
_DRAIN = 1.0


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h")


async def _new(c: httpx.AsyncClient) -> str:
    return (await c.post("/sandboxes", json={"item_id": "i"})).json()["remote_id"]


async def _reap(c: httpx.AsyncClient, rid: str) -> None:
    """What the app's `kill_idle` sends."""
    r = await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
    assert r.status_code == 204
    r = await c.delete(f"/sandboxes/{rid}")
    assert r.status_code == 204


async def test_a_reap_packs_when_the_sandbox_is_torn_down():
    archive = _Archive()
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive
    )
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        assert archive.packs == [], "packed during the write-back, where checkpoints interrupt it"
        await c.delete(f"/sandboxes/{rid}")
    assert archive.packs == ["i"]


async def test_a_checkpoint_between_the_write_back_and_the_teardown_does_not_cancel_the_pack():
    """The case that sank packing inside the write-back: another pod's sweep
    checkpoints the sandbox while the reap is under way."""
    archive = _Archive()
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive
    )
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": False})
        await c.delete(f"/sandboxes/{rid}")
    assert archive.packs == ["i"]


async def test_a_reconciling_write_back_without_pack_withdraws_the_request():
    """The reap's kill never arrived (the app pod died in between) and the
    sandbox went back into use: its next turn-end reconcile does not ask, so a
    later close is a plain teardown again. A checkpoint (above) does not
    withdraw it — that is what made packing in the write-back fail."""
    archive = _Archive()
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive
    )
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})
        await c.delete(f"/sandboxes/{rid}")
    assert archive.packs == []


async def test_a_teardown_nobody_asked_to_pack_does_not():
    """Shutdown, an explicit close, a CAS loser's orphan: their write-backs do
    not ask, and an orphan's dir is not the item's truth."""
    archive = _Archive()
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive
    )
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})
        await c.delete(f"/sandboxes/{rid}")
    assert archive.packs == []


async def test_a_reconcile_the_archive_refused_is_not_followed_by_a_pack():
    archive = _Archive(reconciles=False)
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive
    )
    async with _client(app) as c:
        await _reap(c, await _new(c))
    assert archive.packs == []


async def test_a_checkpoint_still_running_at_teardown_is_waited_for():
    """Round 2: closing turns away what starts LATER; a checkpoint already
    running when the kill arrives — on a big item, almost always one — must be
    let finish, not counted as a reason to give up."""
    archive = _Archive()
    archive.checkpoint_gate = asyncio.Event()
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive
    )
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        checkpoint = asyncio.create_task(
            c.post(f"/sandboxes/{rid}/persist", json={"delete": False})
        )
        await asyncio.wait_for(archive.checkpoint_entered.wait(), 5)
        kill = asyncio.create_task(c.delete(f"/sandboxes/{rid}"))
        await asyncio.sleep(0.1)
        assert archive.packs == [] and not kill.done(), "packed under a running checkpoint"
        archive.checkpoint_gate.set()
        await checkpoint
        assert (await kill).status_code == 204
    assert archive.packs == ["i"]


async def test_a_request_that_outlasts_the_wait_means_no_pack():
    """Began before the teardown and still writing when the wait runs out: the
    sandbox goes without a pack rather than the sweep waiting on it."""
    backend = _BlockingExec()
    archive = _Archive()
    app = make_host_app(backend, advertise_url="http://h", archive=archive, pack_drain_s=0.2)
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        running = asyncio.create_task(c.post(f"/sandboxes/{rid}/exec", json={"cmd": ["w"]}))
        await asyncio.wait_for(backend.entered.wait(), 5)
        try:
            await asyncio.wait_for(c.delete(f"/sandboxes/{rid}"), 5)  # gives up, not hangs
        finally:
            backend.release.set()
            await running
    assert archive.packs == [], "packed while an exec was writing the sandbox"


async def test_the_hosts_own_reaper_does_not_count_itself():
    """`reap_idle` kills from inside the host, not as a request: one exec still
    running is one too many, not the reaper's own share of the count."""
    backend = _BlockingExec()
    archive = _Archive()
    clock = {"t": 0.0}
    app = make_host_app(
        backend,
        advertise_url="http://h",
        archive=archive,
        idle_ttl=10.0,
        clock=lambda: clock["t"],
        pack_drain_s=0.2,
    )
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        running = asyncio.create_task(c.post(f"/sandboxes/{rid}/exec", json={"cmd": ["w"]}))
        await asyncio.wait_for(backend.entered.wait(), 5)
        clock["t"] = 100.0
        try:
            assert await asyncio.wait_for(app.state.controller.reap_idle(), 5) == [rid]
        finally:
            backend.release.set()
            await running
    assert archive.packs == []


async def test_a_kill_with_no_pack_to_make_is_not_closed():
    """Only a teardown that packs closes the sandbox: every other kill — and
    every kill with packing switched off — serves requests until it is gone,
    exactly as before."""

    class _SlowKill(MockSandbox):
        def __init__(self) -> None:
            super().__init__()
            self.killing = asyncio.Event()
            self.finish = asyncio.Event()

        async def kill(self, handle: SandboxHandle) -> None:
            self.killing.set()
            await self.finish.wait()
            await super().kill(handle)

    for archive, pack in ((_Archive(), False), (_Archive(packs=False), True)):
        backend = _SlowKill()
        app = make_host_app(backend, advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive)
        async with _client(app) as c:
            rid = await _new(c)
            await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": pack})
            kill = asyncio.create_task(c.delete(f"/sandboxes/{rid}"))
            await asyncio.wait_for(backend.killing.wait(), 5)
            r = await c.get(f"/sandboxes/{rid}/ready")
            backend.finish.set()
            await kill
        assert r.status_code == 200, (archive.packs_enabled, pack, r.json())


async def test_a_request_during_the_pack_is_turned_away():
    """Closed BEFORE the tar: a checkpoint, a file write, an exec that arrives
    meanwhile is told the sandbox is gone — what it is about to be."""
    archive = _Archive()
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive
    )
    answers: list[tuple[int, str]] = []
    async with _client(app) as c:
        rid = await _new(c)

        async def somebody_writes() -> None:
            r = await c.put(f"/sandboxes/{rid}/file", params={"path": "/x"}, content=b"x")
            answers.append((r.status_code, r.json()["error"]))

        archive.during = somebody_writes
        await _reap(c, rid)
    assert answers == [(404, "SandboxNotFound")]
    assert archive.packs == ["i"]


async def test_a_pack_that_fails_does_not_stop_the_teardown():
    """The pack is an optimisation; the sandbox going away is the reap."""
    backend = MockSandbox()
    archive = _Archive()
    archive.fails = True
    app = make_host_app(backend, advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive)
    async with _client(app) as c:
        rid = await _new(c)
        await _reap(c, rid)
        assert (await c.get(f"/sandboxes/{rid}/ready")).status_code == 404


async def test_a_teardown_that_fails_reopens_the_sandbox():
    """A sandbox whose kill raised is still running — and must still answer, or
    nothing could ever retry the kill."""

    class _Stuck(MockSandbox):
        async def kill(self, handle: SandboxHandle) -> None:
            raise RuntimeError("cgroup busy")

    app = make_host_app(_Stuck(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=_Archive())
    async with _client(app) as c:
        rid = await _new(c)
        with pytest.raises(RuntimeError):
            await c.delete(f"/sandboxes/{rid}")
        assert (await c.get(f"/sandboxes/{rid}/ready")).status_code == 200


async def test_a_sandbox_that_is_not_ready_is_not_packed():
    """Half restored: not the item's workspace."""
    backend = MockSandbox()
    archive = _Archive()
    app = make_host_app(backend, advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive)
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        backend._ready.discard(rid)
        await c.delete(f"/sandboxes/{rid}")
    assert archive.packs == []


async def test_a_failed_request_does_not_leave_the_sandbox_counted_as_busy():
    archive = _Archive()
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=archive
    )
    async with _client(app) as c:
        rid = await _new(c)
        r = await c.get(f"/sandboxes/{rid}/file", params={"path": "/missing"})
        assert r.status_code == 404
        await _reap(c, rid)
    assert archive.packs == ["i"]


async def test_nothing_is_kept_for_a_sandbox_once_it_is_gone():
    """Sandboxes come and go for the life of the host: the count, the request to
    pack and the closed mark are all dropped."""
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", pack_drain_s=_DRAIN, archive=_Archive()
    )
    async with _client(app) as c:
        await _reap(c, await _new(c))
    controller = app.state.controller
    assert controller.in_flight == {}
    assert controller.closing == set() and controller.pack_on_kill == set()
