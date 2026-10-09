"""Slide previews made and kept beside a sandbox (the app's
docs/plan-pptx-preview.md N1, N2, D1, D2): over the wire, and on the real local
backend.

The host converts a deck through the sandbox's own exec and keeps the PDF by
the deck's content hash OUTSIDE the workspace — never walked, archived or
counted, gone when the sandbox is reaped. Only the hash crosses the wire on a
conversion; the PDF is read once, by `GET /preview/{sha}`.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.mock import MockSandbox
from sandbox_host.protocol import PreviewFailed, SandboxSpec

DECK = b"PK\x03\x04 a deck"
SHA = hashlib.sha256(DECK).hexdigest()
FAKE_SOFFICE = ("sh", "-c", 'printf "%%PDF-fake" > "$1/$(basename "${2%.*}").pdf"', "sh")


@pytest.fixture
async def backend():
    return MockSandbox()


@pytest.fixture
async def client(backend):  # noqa: ANN001
    app = make_host_app(backend, advertise_url="http://h:8000")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h") as c:
        yield c


async def _rid(c: httpx.AsyncClient) -> str:
    rid = (await c.post("/sandboxes", json={})).json()["remote_id"]
    await c.put(f"/sandboxes/{rid}/file", params={"path": "/q3.pptx"}, content=DECK)
    return rid


async def test_the_wire_converts_then_serves_the_preview(client) -> None:  # noqa: ANN001
    rid = await _rid(client)
    ask = {"path": "/q3.pptx"}

    looked = await client.post(f"/sandboxes/{rid}/preview", params={**ask, "convert": "false"})
    assert looked.json() == {"sha": None}
    # 204, not 404: on this wire 404 means "no such sandbox".
    assert (await client.get(f"/sandboxes/{rid}/preview/{SHA}")).status_code == 204
    made = await client.post(f"/sandboxes/{rid}/preview", params={**ask, "convert": "true"})
    got = await client.get(f"/sandboxes/{rid}/preview/{SHA}")

    assert made.json() == {"sha": SHA}
    assert got.status_code == 200 and got.content.startswith(b"%PDF")


async def test_a_failed_conversion_is_named_on_the_wire(client, backend) -> None:  # noqa: ANN001
    backend.fail_preview = "source file could not be loaded"
    rid = await _rid(client)

    r = await client.post(
        f"/sandboxes/{rid}/preview", params={"path": "/q3.pptx", "convert": "true"}
    )

    assert r.status_code == 404
    assert r.json() == {"error": "PreviewFailed", "detail": "source file could not be loaded"}


@pytest.mark.parametrize(
    ("key", "status"),
    # An encoded `/` never matches the route; any name that does is a 422.
    [("..%2F..%2Fetc%2Fpasswd", 404), ("A" * 64, 422), ("abc", 422)],
)
async def test_the_wire_refuses_a_name_that_is_not_a_content_hash(client, key, status) -> None:  # noqa: ANN001
    rid = await _rid(client)

    assert (await client.get(f"/sandboxes/{rid}/preview/{key}")).status_code == status


def _local(tmp_path, command=FAKE_SOFFICE) -> LocalProcessSandbox:  # noqa: ANN001
    return LocalProcessSandbox(root_dir=tmp_path, isolate=False, preview_command=command)


async def test_a_preview_lives_beside_the_workspace_not_in_it(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")
    before = await sandbox.disk_usage(h)

    assert await sandbox.render_preview(h, "/q3.pptx", convert=True) == SHA

    assert (await sandbox.get_preview(h, SHA) or b"").startswith(b"%PDF")
    assert [e.path for e in (await sandbox.walk(h, "/")).files] == ["/q3.pptx"]
    assert await sandbox.disk_usage(h) == before
    assert not list(tmp_path.rglob(".preview-out/*/*.pdf"))


async def test_a_failed_conversion_says_why_and_caches_nothing(tmp_path) -> None:  # noqa: ANN001
    failing = ("sh", "-c", "echo 'source file could not be loaded' >&2; exit 1", "sh")
    sandbox = _local(tmp_path, failing)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")

    with pytest.raises(PreviewFailed, match="could not be loaded"):
        await sandbox.render_preview(h, "/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None


async def test_a_reaped_sandbox_takes_its_previews_with_it(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")
    await sandbox.render_preview(h, "/q3.pptx", convert=True)
    stored = list(tmp_path.rglob(f"{SHA}.pdf"))
    assert len(stored) == 1

    await sandbox.kill(h)

    assert not stored[0].exists()


async def test_without_convert_a_missing_preview_is_none_and_nothing_runs(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path, ("sh", "-c", "exit 1", "sh"))
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")

    assert await sandbox.render_preview(h, "/q3.pptx", convert=False) is None


async def test_a_converter_that_exits_non_zero_is_a_failure_even_with_a_pdf(tmp_path) -> None:  # noqa: ANN001
    half = (
        "sh",
        "-c",
        'printf "%%PDF-half" > "$1/$(basename "${2%.*}").pdf"; echo crashed >&2; exit 1',
        "sh",
    )
    sandbox = _local(tmp_path, half)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")

    with pytest.raises(PreviewFailed, match="crashed"):
        await sandbox.render_preview(h, "/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None
    assert not list(tmp_path.rglob(f".preview-out/{SHA}"))
