"""The slide-preview cache beside a sandbox (docs/plan-pptx-preview.md N2, D1).

A converted PDF is kept OUTSIDE the workspace, named by the deck's content
hash: never in the file tree, never synced, never counted against the
workspace, and gone when the sandbox is reaped. Every backend keeps the same
promise, so one set of tests runs against each.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from httpx import ASGITransport

from workspace_app.sandbox.http_client import HttpSandbox
from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec

SHA = hashlib.sha256(b"deck").hexdigest()


@pytest.fixture(params=["mock", "local", "http"])
async def sandbox(request, tmp_path):  # noqa: ANN001, ANN201
    if request.param == "mock":
        yield MockSandbox()
    elif request.param == "local":
        yield LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    else:
        from tests.sandbox.test_http import _ADVERTISE, _fake_host

        app = _fake_host(MockSandbox(), _ADVERTISE)
        async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
            yield HttpSandbox(base_url=_ADVERTISE, client=client)


async def test_a_preview_not_yet_made_is_none(sandbox) -> None:  # noqa: ANN001
    h = await sandbox.create(SandboxSpec())

    assert await sandbox.get_preview(h, SHA) is None


async def test_a_stored_preview_comes_back_byte_for_byte(sandbox) -> None:  # noqa: ANN001
    h = await sandbox.create(SandboxSpec())
    pdf = b"%PDF-1.7\n\x00\xff binary"

    await sandbox.put_preview(h, SHA, pdf)

    assert await sandbox.get_preview(h, SHA) == pdf


async def test_a_preview_is_not_a_workspace_file(sandbox) -> None:  # noqa: ANN001
    h = await sandbox.create(SandboxSpec())

    await sandbox.put_preview(h, SHA, b"%PDF")

    walked = await sandbox.walk(h, "/")
    assert walked.files == [] and walked.dirs == []


async def test_a_preview_is_not_charged_to_the_workspace(tmp_path) -> None:  # noqa: ANN001
    """Measured where the quota measures (`disk_usage`), on a real disk."""
    sandbox = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    h = await sandbox.create(SandboxSpec())
    before = await sandbox.disk_usage(h)

    await sandbox.put_preview(h, SHA, b"%PDF" * 100_000)

    assert await sandbox.disk_usage(h) == before


@pytest.mark.parametrize("key", ["../../etc/passwd", "A" * 64, "abc", SHA + "/x", ""])
async def test_only_a_content_hash_names_a_preview(sandbox, key: str) -> None:  # noqa: ANN001
    """The name reaches a path on disk; nothing but 64 lowercase hex may."""
    h = await sandbox.create(SandboxSpec())

    with pytest.raises(ValueError):
        await sandbox.put_preview(h, key, b"%PDF")
    with pytest.raises(ValueError):
        await sandbox.get_preview(h, key)


async def test_a_reaped_sandbox_takes_its_previews_with_it(sandbox) -> None:  # noqa: ANN001
    h = await sandbox.create(SandboxSpec(), sandbox_id="item-1")
    await sandbox.put_preview(h, SHA, b"%PDF")

    await sandbox.kill(h)
    again = await sandbox.create(SandboxSpec(), sandbox_id="item-1")

    assert await sandbox.get_preview(again, SHA) is None
