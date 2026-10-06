"""The host half of the pack decision (docs/plan-archive-pack.md, decisions 3–4).

The archive packs only when the controller hands it a guard, and the guard is
the controller's answer to "is anyone touching this sandbox right now?". The
generation sees writes to the TREE; only the host sees requests to the SANDBOX
— and an `exec` is a STREAMING response, so "a request is in flight" has to
last until the stream ends, not until the handler returns.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.mock import MockSandbox
from sandbox_host.protocol import ExecResult, SandboxHandle


class _Archive:
    """Records each persist and the guard's answer AT that moment, and keeps the
    guard so a test can ask it again later."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, bool | None]] = []
        # Run between the guard's first and second question — standing in for
        # the tar — so a test can put a request exactly there.
        self.during = None
        self.second: list[bool] = []

    async def restore(self, item_id: str, workspace_dir: Path) -> bool:
        return False

    async def persist(self, item_id, workspace_dir, *, delete, pack_guard=None) -> None:
        self.calls.append((delete, None if pack_guard is None else pack_guard()))
        if pack_guard is not None and self.during is not None:
            await self.during()
            self.second.append(pack_guard())


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


async def test_a_reap_persist_asks_for_a_pack_and_an_idle_sandbox_allows_it():
    archive = _Archive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i"})).json()["remote_id"]
        r = await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        assert r.status_code == 204
    assert archive.calls == [(True, True)]


async def test_without_pack_no_guard_is_given():
    archive = _Archive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i"})).json()["remote_id"]
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})
    assert archive.calls == [(True, None)]


async def test_an_exec_still_streaming_makes_the_guard_refuse():
    """The case a request-start timestamp cannot see: the exec began BEFORE the
    persist and is still writing while the tar would read."""
    backend = _BlockingExec()
    archive = _Archive()
    app = make_host_app(backend, advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i"})).json()["remote_id"]
        running = asyncio.create_task(c.post(f"/sandboxes/{rid}/exec", json={"cmd": ["sleep"]}))
        await asyncio.wait_for(backend.entered.wait(), 5)
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
        backend.release.set()
        await running
    assert archive.calls == [(True, False)], "packed while an exec was writing the sandbox"


async def test_a_request_that_came_and_went_during_the_pack_makes_the_guard_refuse():
    """Started and finished between the guard's two questions: the count is back
    to one, so only the activity timestamp can tell."""
    ticks = iter(range(1, 10_000))
    archive = _Archive()
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", archive=archive, clock=lambda: next(ticks)
    )
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i"})).json()["remote_id"]

        async def somebody_reads() -> None:
            await c.get(f"/sandboxes/{rid}/ready")

        archive.during = somebody_reads
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
    assert archive.calls == [(True, True)], "the guard refused before anything happened"
    assert archive.second == [False], "a request during the pack went unnoticed"


async def test_the_guard_holds_through_a_pack_nobody_interrupts():
    """The control for the test above: same harness, nothing in between."""

    async def nothing() -> None:
        return None

    archive = _Archive()
    archive.during = nothing
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i"})).json()["remote_id"]
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
    assert archive.second == [True]


async def test_a_failed_request_does_not_leave_the_sandbox_counted_as_busy():
    archive = _Archive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i"})).json()["remote_id"]
        r = await c.get(f"/sandboxes/{rid}/file", params={"path": "/missing"})
        assert r.status_code == 404
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True, "pack": True})
    assert archive.calls == [(True, True)]
