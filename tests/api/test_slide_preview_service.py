"""The order `SlidePreviews` asks its questions in (docs/plan-pptx-preview.md
N2, N6, D3, D4): a cached preview never wakes the sandbox, a big deck the
person has not said yes to never wakes it either, and two askers convert once.
"""

from __future__ import annotations

import asyncio

from workspace_app.api import slide_preview
from workspace_app.api.slide_preview import NeedsConfirm, Preview, SlidePreviews
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec

DECK = b"PK\x03\x04 a deck"


class _Files:
    """`file_size` only — what the service asks of the workspace facade."""

    def __init__(self, sizes: dict[str, int]) -> None:
        self.sizes = sizes

    async def file_size(self, _item: str, path: str) -> int | None:
        return self.sizes.get(path)


class _Registry:
    """The two doors: a handle WITHOUT waking (`resolve_io_handle`), and one
    that wakes (`ensure_handle`) — counted, since waking is the cost."""

    def __init__(self, sandbox: MockSandbox, live) -> None:  # noqa: ANN001
        self.sandbox = sandbox
        self.live = live
        self.wakes = 0

    async def resolve_io_handle(self, _item: str):  # noqa: ANN202
        return self.live

    async def session(self, item: str) -> str:
        return item

    async def ensure_handle(self, _session: str):  # noqa: ANN202
        self.wakes += 1
        if self.live is None:
            self.live = await self.sandbox.create(SandboxSpec())
            await self.sandbox.upload(self.live, DECK, "/q3.pptx")
        return self.live


class _Gate:
    """The per-person sandbox limit: refuses when `full`, counts every ask."""

    def __init__(self, *, full: bool) -> None:
        self.full = full
        self.asked = 0

    async def check(self, item: str) -> None:
        from workspace_app.quota.admission import SandboxQuotaExceeded

        self.asked += 1
        if self.full:
            raise SandboxQuotaExceeded("alice", "sandboxes", 1, 1)


async def _service(*, live: bool, gate: _Gate | None = None):  # noqa: ANN202
    sandbox = MockSandbox()
    handle = None
    if live:
        handle = await sandbox.create(SandboxSpec())
        await sandbox.upload(handle, DECK, "/q3.pptx")
    registry = _Registry(sandbox, handle)
    files = _Files({"/q3.pptx": len(DECK)})
    svc = SlidePreviews(files=files, registry=registry, sandbox=sandbox, admission=gate)  # ty: ignore[invalid-argument-type]
    return svc, registry, sandbox


async def test_a_cached_preview_is_served_without_waking_the_sandbox() -> None:
    svc, registry, _ = await _service(live=True)
    assert isinstance(await svc.preview("i", "/q3.pptx", confirm=False), Preview)
    wakes = registry.wakes

    again = await svc.preview("i", "/q3.pptx", confirm=False)

    assert isinstance(again, Preview) and registry.wakes == wakes


async def test_a_big_deck_not_yet_said_yes_to_wakes_nothing(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(slide_preview, "CONFIRM_BYTES", 1)
    svc, registry, _ = await _service(live=False)

    got = await svc.preview("i", "/q3.pptx", confirm=False)

    assert got == NeedsConfirm(size=len(DECK), limit=1)
    assert registry.wakes == 0


async def test_a_cold_sandbox_is_woken_to_convert() -> None:
    svc, registry, _ = await _service(live=False)

    got = await svc.preview("i", "/q3.pptx", confirm=False)

    assert isinstance(got, Preview) and registry.wakes == 1


async def test_two_askers_at_once_convert_the_deck_once() -> None:
    """D4: the second waits for the first and then finds the cache filled."""
    svc, _, sandbox = await _service(live=True)

    a, b = await asyncio.gather(
        svc.preview("i", "/q3.pptx", confirm=False), svc.preview("i", "/q3.pptx", confirm=False)
    )

    assert isinstance(a, Preview) and isinstance(b, Preview)
    assert sandbox.preview_conversions == 1


async def test_waking_a_sandbox_to_convert_is_held_to_the_sandbox_limit() -> None:
    """A preview opens a sandbox like a terminal does, so it passes the same gate."""
    import pytest

    from workspace_app.quota.admission import SandboxQuotaExceeded

    gate = _Gate(full=True)
    svc, registry, _ = await _service(live=False, gate=gate)

    with pytest.raises(SandboxQuotaExceeded):
        await svc.preview("i", "/q3.pptx", confirm=False)

    assert registry.wakes == 0


async def test_a_cached_preview_does_not_ask_the_sandbox_limit() -> None:
    gate = _Gate(full=False)
    svc, _, _ = await _service(live=True, gate=gate)
    await svc.preview("i", "/q3.pptx", confirm=False)
    asked = gate.asked
    gate.full = True

    assert isinstance(await svc.preview("i", "/q3.pptx", confirm=False), Preview)
    assert gate.asked == asked


async def test_no_lock_outlives_its_conversion() -> None:
    """One lock per (item, path) ever previewed would grow for the pod's life."""
    svc, _, _ = await _service(live=True)

    await asyncio.gather(
        svc.preview("i", "/q3.pptx", confirm=False), svc.preview("i", "/q3.pptx", confirm=False)
    )

    assert svc._locks == {}
