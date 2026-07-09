"""#492 P3 host wiring: item-aware create restores from the NFS archive, and
POST /sandboxes/{rid}/persist rsyncs the live dir back. Exercised over the HTTP
shell with MockSandbox + a fake archive (the real rsync is covered in
test_nfs_archive*), so this pins the CONTROLLER/endpoint plumbing: which item +
dir + delete flag reach the archive, and that no archive ⇒ clean no-ops.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.mock import MockSandbox


class _FakeArchive:
    def __init__(self) -> None:
        self.restored: list[tuple[str, str]] = []
        self.persisted: list[tuple[str, str, bool]] = []
        self.has_archive_for: set[str] = set()  # items with existing archive data

    async def restore(self, item_id: str, workspace_dir: Path) -> bool:
        self.restored.append((item_id, str(workspace_dir)))
        return item_id in self.has_archive_for

    async def persist(self, item_id: str, workspace_dir: Path, *, delete: bool) -> None:
        self.persisted.append((item_id, str(workspace_dir), delete))


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h")


async def test_create_with_item_id_restores_from_archive():
    archive = _FakeArchive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        r = await c.post("/sandboxes", json={"item_id": "item-42"})
        assert r.status_code == 200
    assert [i for i, _ in archive.restored] == ["item-42"]
    # restored into the sandbox's own local working dir
    assert archive.restored[0][1].endswith("/root")


async def test_create_without_item_id_does_not_touch_archive():
    archive = _FakeArchive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        r = await c.post("/sandboxes", json={})
        assert r.status_code == 200
    assert archive.restored == []


async def test_persist_rsyncs_the_item_with_delete_flag():
    archive = _FakeArchive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-42"})).json()["remote_id"]
        assert (await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})).status_code == 204
        assert (
            await c.post(f"/sandboxes/{rid}/persist", json={"delete": False})
        ).status_code == 204
    assert [(i, d) for i, _, d in archive.persisted] == [("item-42", True), ("item-42", False)]


async def test_persist_defaults_to_upload_only():
    archive = _FakeArchive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i"})).json()["remote_id"]
        await c.post(f"/sandboxes/{rid}/persist", json={})  # no delete key
    assert archive.persisted[0][2] is False


async def test_persist_is_a_noop_without_an_item_mapping():
    archive = _FakeArchive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={})).json()["remote_id"]  # no item_id
        assert (await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})).status_code == 204
    assert archive.persisted == []


async def test_persist_is_a_noop_when_no_archive_configured():
    app = make_host_app(MockSandbox(), advertise_url="http://h")  # archive=None
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i"})).json()["remote_id"]
        assert (await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})).status_code == 204


async def test_kill_drops_the_item_mapping_so_a_later_persist_is_a_noop():
    archive = _FakeArchive()
    app = make_host_app(MockSandbox(), advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-42"})).json()["remote_id"]
        await c.delete(f"/sandboxes/{rid}")
        await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})
    assert archive.persisted == []


async def test_create_with_item_reowns_the_restored_workspace():
    """#504: the bulk rsync restore writes as root; the controller must reown the
    restored tree to the sandbox uid before marking it ready."""
    backend = MockSandbox()
    app = make_host_app(backend, advertise_url="http://h", archive=_FakeArchive())
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-42"})).json()["remote_id"]
    assert backend.reowned == [rid]


async def test_create_without_archive_does_not_reown():
    backend = MockSandbox()
    app = make_host_app(backend, advertise_url="http://h")  # archive=None
    async with _client(app) as c:
        await c.post("/sandboxes", json={"item_id": "item-42"})
    assert backend.reowned == []


async def test_create_without_item_does_not_reown():
    backend = MockSandbox()
    app = make_host_app(backend, advertise_url="http://h", archive=_FakeArchive())
    async with _client(app) as c:
        await c.post("/sandboxes", json={})  # no item_id
    assert backend.reowned == []


async def test_create_with_item_marks_the_sandbox_ready():
    """#492: rsync restore is synchronous, so the host marks the archive-restored
    sandbox ready itself (the app no longer runs its own restore)."""
    backend = MockSandbox()
    app = make_host_app(backend, advertise_url="http://h", archive=_FakeArchive())
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-42"})).json()["remote_id"]
        assert (await c.get(f"/sandboxes/{rid}/ready")).json()["ready"] is True


async def test_persist_is_gated_on_readiness():
    """#492 Q9: persist must NOT push a not-ready (half-restored) dir back over
    the archive — else a --delete could wipe durable data."""
    backend = MockSandbox()
    archive = _FakeArchive()
    app = make_host_app(backend, advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-42"})).json()["remote_id"]
        backend._ready.discard(rid)  # simulate a mid-restore / not-yet-ready sandbox
        assert (await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})).status_code == 204
    assert archive.persisted == []  # gated out
