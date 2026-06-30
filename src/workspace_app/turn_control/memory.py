"""In-memory ITurnControl — the default backend for tests and single-pod runs.

A plain dict of per-key epochs. ``advance`` is atomic under asyncio because it
neither awaits nor yields between the read and the write. Two ``ChatTurnEngine``
instances sharing ONE of these simulate two pods over a shared backend, which is
how the cross-pod cancel tests run without any real broker.
"""

from __future__ import annotations

from .base import ITurnControl


class InMemoryTurnControl(ITurnControl):
    def __init__(self) -> None:
        self._epochs: dict[str, int] = {}

    async def current(self, key: str) -> int:
        return self._epochs.get(key, 0)

    async def advance(self, key: str) -> int:
        nxt = self._epochs.get(key, 0) + 1
        self._epochs[key] = nxt
        return nxt
