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

    def __init__(self, *, reconciles: bool = True) -> None:
        self.reconciles = reconciles
        self.persists: list[bool] = []
        self.packs: list[str] = []
        self.during = None
        self.fails = False

    async def restore(self, item_id: str, workspace_dir: Path) -> bool:
        return False

    async def persist(self, item_id, workspace_dir, *, delete) -> bool:
        self.persists.append(delete)
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
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
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
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": False})
        await c.delete(f"/sandboxes/{rid}")
    assert archive.packs == ["i"]


async def test_a_teardown_nobody_asked_to_pack_does_not():
    """Shutdown, an explicit close, a CAS loser's orphan: their write-backs do
    not ask, and an orphan's dir is not the item's truth."""
    archive = _Archive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})
        await c.delete(f"/sandboxes/{rid}")
    assert archive.packs == []


async def test_a_reconcile_the_archive_refused_is_not_followed_by_a_pack():
    archive = _Archive(reconciles=False)
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        await _reap(c, await _new(c))
    assert archive.packs == []


async def test_an_exec_still_streaming_at_teardown_means_no_pack():
    """Began before the teardown and still writing: the in-flight count sees it."""
    backend = _BlockingExec()
    archive = _Archive()
    app = make_host_app(backend, advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        running = asyncio.create_task(c.post(f"/sandboxes/{rid}/exec", json={"cmd": ["w"]}))
        await asyncio.wait_for(backend.entered.wait(), 5)
        await c.delete(f"/sandboxes/{rid}")
        backend.release.set()
        await running
    assert archive.packs == [], "packed while an exec was writing the sandbox"


async def test_a_request_during_the_pack_is_turned_away():
    """Closed BEFORE the tar: a checkpoint, a file write, an exec that arrives
    meanwhile is told the sandbox is gone — what it is about to be."""
    archive = _Archive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
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
    app = make_host_app(backend, advertise_url="http://h", archive=archive)
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

    app = make_host_app(_Stuck(), advertise_url="http://h", archive=_Archive())
    async with _client(app) as c:
        rid = await _new(c)
        with pytest.raises(RuntimeError):
            await c.delete(f"/sandboxes/{rid}")
        assert (await c.get(f"/sandboxes/{rid}/ready")).status_code == 200


async def test_a_sandbox_that_is_not_ready_is_not_packed():
    """Half restored: not the item's workspace."""
    backend = MockSandbox()
    archive = _Archive()
    app = make_host_app(backend, advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = await _new(c)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        backend._ready.discard(rid)
        await c.delete(f"/sandboxes/{rid}")
    assert archive.packs == []


async def test_a_failed_request_does_not_leave_the_sandbox_counted_as_busy():
    archive = _Archive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = await _new(c)
        r = await c.get(f"/sandboxes/{rid}/file", params={"path": "/missing"})
        assert r.status_code == 404
        await _reap(c, rid)
    assert archive.packs == ["i"]


async def test_nothing_is_kept_for_a_sandbox_once_it_is_gone():
    """Sandboxes come and go for the life of the host: the count, the request to
    pack and the closed mark are all dropped."""
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=_Archive())
    async with _client(app) as c:
        await _reap(c, await _new(c))
    controller = app.state.controller
    assert controller.in_flight == {}
    assert controller.closing == set() and controller.pack_on_kill == set()
