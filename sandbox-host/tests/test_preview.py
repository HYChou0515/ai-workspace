"""The slide-preview cache beside a sandbox (the app's docs/plan-pptx-preview.md
N2, D1): over the wire, and on the real local backend.

A converted deck is stored by its content hash OUTSIDE the workspace — never
walked, archived or counted — and goes when the sandbox is reaped.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.mock import MockSandbox
from sandbox_host.protocol import SandboxSpec

SHA = hashlib.sha256(b"deck").hexdigest()


@pytest.fixture
async def client():
    app = make_host_app(MockSandbox(), advertise_url="http://h:8000")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h") as c:
        yield c


async def _rid(c: httpx.AsyncClient) -> str:
    return (await c.post("/sandboxes", json={})).json()["remote_id"]


async def test_the_wire_stores_and_returns_a_preview(client) -> None:  # noqa: ANN001
    rid = await _rid(client)
    pdf = b"%PDF-1.7\n\x00\xff"

    # 204, not 404: on this wire 404 means "no such sandbox".
    assert (await client.get(f"/sandboxes/{rid}/preview/{SHA}")).status_code == 204
    assert (await client.put(f"/sandboxes/{rid}/preview/{SHA}", content=pdf)).status_code == 204
    got = await client.get(f"/sandboxes/{rid}/preview/{SHA}")

    assert got.status_code == 200 and got.content == pdf


@pytest.mark.parametrize(
    ("key", "status"),
    # An encoded `/` never matches the route; any name that does is a 422.
    [("..%2F..%2Fetc%2Fpasswd", 404), ("A" * 64, 422), ("abc", 422)],
)
async def test_the_wire_refuses_a_name_that_is_not_a_content_hash(client, key, status) -> None:  # noqa: ANN001
    rid = await _rid(client)

    assert (await client.put(f"/sandboxes/{rid}/preview/{key}", content=b"x")).status_code == status
    assert (await client.get(f"/sandboxes/{rid}/preview/{key}")).status_code == status


@pytest.fixture
def sandbox(tmp_path) -> LocalProcessSandbox:  # noqa: ANN001
    return LocalProcessSandbox(root_dir=tmp_path, isolate=False)


async def test_a_preview_lives_beside_the_workspace_not_in_it(sandbox) -> None:  # noqa: ANN001
    h = await sandbox.create(SandboxSpec())
    before = await sandbox.disk_usage(h)

    await sandbox.put_preview(h, SHA, b"%PDF" * 100_000)

    assert await sandbox.get_preview(h, SHA) == b"%PDF" * 100_000
    assert (await sandbox.walk(h, "/")).files == []
    assert await sandbox.disk_usage(h) == before


async def test_a_reaped_sandbox_takes_its_previews_with_it(sandbox, tmp_path) -> None:  # noqa: ANN001
    h = await sandbox.create(SandboxSpec())
    await sandbox.put_preview(h, SHA, b"%PDF")
    stored = list(tmp_path.rglob(f"{SHA}.pdf"))
    assert len(stored) == 1

    await sandbox.kill(h)

    assert not stored[0].exists()
