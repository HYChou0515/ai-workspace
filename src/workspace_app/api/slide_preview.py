"""A slide deck in the workspace, shown as a PDF (docs/plan-pptx-preview.md).

The item's sandbox converts the deck — through its own exec, under its own uid
and limits (N1) — and keeps the PDF beside the workspace under the deck's
content hash (N2), so an edited deck is simply a new preview and the cache
dies with the sandbox. This module decides the order of the questions:

1. is it a deck at all (N4), and does it exist;
2. is its preview already there — asked WITHOUT waking a cold sandbox;
3. if not, is it big enough that the person must say yes first (N6);
4. then wake the sandbox and convert, once per deck at a time (D4).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ..sandbox.protocol import SandboxNotFound

if TYPE_CHECKING:
    from ..files.facade import WorkspaceFiles
    from ..sandbox.protocol import Sandbox
    from .registry import InvestigationRegistry

#: N4: the decks LibreOffice Impress (the one LibreOffice the sandbox carries)
#: converts.
SLIDE_EXTENSIONS = frozenset({"pptx", "ppt", "odp"})

#: N6: above this many bytes, a deck not yet converted is converted only after
#: the person says yes. The server's number; the page asks with what it says.
CONFIRM_BYTES = 20 * 1024 * 1024


class NotASlideDeck(ValueError):
    """The path is not a format this preview converts (N4)."""


@dataclass(frozen=True)
class Preview:
    pdf: bytes


@dataclass(frozen=True)
class NeedsConfirm:
    size: int
    limit: int


def is_slide_deck(path: str) -> bool:
    return PurePosixPath(path).suffix.lower().lstrip(".") in SLIDE_EXTENSIONS


class SlidePreviews:
    def __init__(
        self, *, files: WorkspaceFiles, registry: InvestigationRegistry, sandbox: Sandbox
    ) -> None:
        self._files = files
        self._registry = registry
        self._sandbox = sandbox
        # D4: one conversion of a deck at a time on this pod; a second asker
        # waits for the first and then finds the cache filled.
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def preview(self, item_id: str, path: str, *, confirm: bool) -> Preview | NeedsConfirm:
        """The deck's PDF, or `NeedsConfirm` when it is big and not yet
        converted. Raises `NotASlideDeck`, `FileNotFoundError`, and the
        sandbox's `PreviewFailed`."""
        if not is_slide_deck(path):
            raise NotASlideDeck(path)
        size = await self._files.file_size(item_id, path)
        if size is None:
            raise FileNotFoundError(path)
        cached = await self._cached(item_id, path)
        if cached is not None:
            return Preview(cached)
        if size > CONFIRM_BYTES and not confirm:
            return NeedsConfirm(size=size, limit=CONFIRM_BYTES)
        lock = self._locks.setdefault((item_id, path), asyncio.Lock())
        async with lock:
            session = await self._registry.session(item_id)
            handle = await self._registry.ensure_handle(session)
            sha = await self._sandbox.render_preview(handle, path, convert=True)
            pdf = await self._sandbox.get_preview(handle, sha) if sha else None
        if pdf is None:  # pragma: no cover — reaped between the two calls
            raise SandboxNotFound(f"the sandbox of {item_id} went away while converting {path}")
        return Preview(pdf)

    async def _cached(self, item_id: str, path: str) -> bytes | None:
        """The preview if this deck's is already beside a live sandbox — never
        waking one: the cache dies with its sandbox, so a cold one has none."""
        handle = await self._registry.resolve_io_handle(item_id)
        if handle is None:
            return None
        try:
            sha = await self._sandbox.render_preview(handle, path, convert=False)
            return await self._sandbox.get_preview(handle, sha) if sha else None
        except (SandboxNotFound, FileNotFoundError):
            return None
