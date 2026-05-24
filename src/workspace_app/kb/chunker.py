"""Chunker Protocol + implementations — split a document's canonical text into
retrievable chunks. Pluggable (fixed-token, LLM-assisted, …) like the app's
other layers.

A Chunk records the verbatim source span (`start`/`end` into the canonical
text, for citation highlight) and its `text` (what gets embedded; an
implementation may fold in structural context, so it need not equal the span).
"""

from __future__ import annotations

import re
from typing import Protocol

from msgspec import Struct

_TOKEN = re.compile(r"\S+")


class Chunk(Struct, frozen=True):
    seq: int  # 0-based order within the document
    start: int  # inclusive char offset into the canonical text
    end: int  # exclusive char offset
    text: str


class Chunker(Protocol):
    def chunk(self, text: str) -> list[Chunk]: ...


class FixedTokenChunker:
    """Fixed-size sliding window over whitespace tokens, with overlap. Token
    spans map back to char offsets so each chunk's text is a verbatim slice."""

    def __init__(self, max_tokens: int = 256, overlap_tokens: int = 32) -> None:
        self._max = max_tokens
        self._overlap = overlap_tokens

    def chunk(self, text: str) -> list[Chunk]:
        spans = [(m.start(), m.end()) for m in _TOKEN.finditer(text)]
        if not spans:
            return []
        step = max(1, self._max - self._overlap)
        n = len(spans)
        chunks: list[Chunk] = []
        i = 0
        while True:  # spans is non-empty → always runs ≥1 time; exits via break
            end_idx = min(i + self._max, n)
            start, end = spans[i][0], spans[end_idx - 1][1]
            chunks.append(Chunk(seq=len(chunks), start=start, end=end, text=text[start:end]))
            if end_idx >= n:
                break
            i += step
        return chunks
