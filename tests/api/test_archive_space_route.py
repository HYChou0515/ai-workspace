"""docs/plan-archive-full.md P6 (D6): the capacity page shows the archive's use
any time a superuser looks — not only when a notification arrives.

Its own endpoint rather than a field of the capacity summary: the summary is a
whole-period analysis that can be slow or refuse (too many rows), and this line
is the one that must still be there the day the archive is full."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from httpx import ASGITransport

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.archive_health import ArchiveSpace
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient

WHO = {"id": "root"}


def _disk(free: int, total: int):  # noqa: ANN202
    def usage(_path: Any) -> Any:
        return shutil._ntuple_diskusage(total, total - free, free)  # type: ignore[attr-defined]

    return usage


def _app(space: ArchiveSpace | None):  # noqa: ANN202
    WHO["id"] = "root"
    spec = make_spec(default_user=lambda: WHO["id"])
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: WHO["id"],
        superusers=frozenset({"root"}),
        archive_space=space,
        archive_warn_free_ratio=0.2,
        archive_block_free_ratio=0.1,
    )


def _client(app) -> AsyncClient:  # noqa: ANN001
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_a_superuser_sees_the_archives_use(tmp_path: Path):
    app = _app(ArchiveSpace(tmp_path, usage=_disk(free=300, total=1000), max_age_s=0))
    async with _client(app) as c:
        r = await c.get("/api/admin/capacity/archive")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "configured": True,
        "free": 300,
        "total": 1000,
        "warn_free_ratio": 0.2,
        "block_free_ratio": 0.1,
    }


async def test_with_no_archive_it_says_so(tmp_path: Path):
    async with _client(_app(None)) as c:
        r = await c.get("/api/admin/capacity/archive")
    assert r.status_code == 200
    assert r.json()["configured"] is False


async def test_only_a_superuser_may_look(tmp_path: Path):
    app = _app(ArchiveSpace(tmp_path, usage=_disk(free=300, total=1000), max_age_s=0))
    WHO["id"] = "alice"
    async with _client(app) as c:
        r = await c.get("/api/admin/capacity/archive")
    assert r.status_code == 403
