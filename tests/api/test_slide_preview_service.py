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


async def _service(*, live: bool):  # noqa: ANN202
    sandbox = MockSandbox()
    handle = None
    if live:
        handle = await sandbox.create(SandboxSpec())
        await sandbox.upload(handle, DECK, "/q3.pptx")
    registry = _Registry(sandbox, handle)
    files = _Files({"/q3.pptx": len(DECK)})
    return SlidePreviews(files=files, registry=registry, sandbox=sandbox), registry, sandbox  # ty: ignore[invalid-argument-type]


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
