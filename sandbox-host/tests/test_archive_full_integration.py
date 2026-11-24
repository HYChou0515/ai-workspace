"""docs/plan-archive-full.md P1, for real: an archive that is actually full.

Runs only where `ARCHIVE_TINY_FS` names a small, writable filesystem — mounting
one needs no root inside a container (`docker run --tmpfs /tiny:size=64k
-e ARCHIVE_TINY_FS=/tiny …`). Real rsync, real `LocalProcessSandbox`, the real
`/persist` route: what the app receives is what production would have received
the day the PVC filled."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.nfs_archive import NfsArchive

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("ARCHIVE_TINY_FS") or shutil.which("rsync") is None,
        reason="needs ARCHIVE_TINY_FS (a small filesystem) and rsync",
    ),
]


async def test_a_full_archive_answers_persist_with_no_space_left(tmp_path: Path):
    tiny = Path(os.environ["ARCHIVE_TINY_FS"])
    free = shutil.disk_usage(tiny).free
    archive = NfsArchive(tiny)
    sandbox = LocalProcessSandbox(root_dir=tmp_path / "live", isolate=False)
    app = make_host_app(sandbox, advertise_url="http://h", archive=archive)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h") as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-1"})).json()["remote_id"]
        # A file bigger than everything the archive has left.
        work = sandbox.workspace_dir(sandbox_handle(rid))
        (work / "big.bin").write_bytes(os.urandom(free + 64 * 1024))
        r = await c.post(f"/sandboxes/{rid}/persist", json={"delete": False})

    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "ArchiveWriteFailed"
    assert "No space left on device" in body["detail"]


def sandbox_handle(rid: str):
    from sandbox_host.protocol import SandboxHandle

    return SandboxHandle(id=rid)
