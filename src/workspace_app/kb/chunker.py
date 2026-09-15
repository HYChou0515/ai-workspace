"""Chunker Protocol + implementations — split a document's canonical text into
retrievable chunks. Pluggable (fixed-token, LLM-assisted, …) like the app's
other layers.

A Chunk records the verbatim source span (`start`/`end` into the canonical
text, for citation highlight) and its `text` (what gets embedded; an
implementation may fold in structural context, so it need not equal the span).
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from msgspec import Struct

from .tokens import CJK_RANGES

# A token is ONE CJK character, or a maximal run of other non-whitespace. CJK
# scripts have no word spaces, so a whitespace tokenizer (`\S+`) made a whole
# line — or a whole paragraph — one token: measured ON THIS CHUNKER, a 12k-char
# Chinese document came out as ONE chunk (plan-rag-context P1). Production does
# not use this class — it wires the LlamaIndex pipeline (`kb_pipeline`), whose
# SentenceSplitter chunks Chinese at ~154 chars — so this fix reaches only the
# legacy / offline path. The character class is `tokens.py`'s, so the
# "≈ N tokens" estimate and this chunker agree on what a CJK token is.
_TOKEN = re.compile(f"[{CJK_RANGES}]|[^\\s{CJK_RANGES}]+")

logger = logging.getLogger(__name__)


class Chunk(Struct, frozen=True):
    seq: int  # 0-based order within the document
    start: int  # inclusive char offset into the canonical text
    end: int  # exclusive char offset
    text: str


class Chunker(Protocol):
    """Splits a document's canonical text into retrievable chunks. Implement
    `chunk` to swap the strategy (markdown-structure-aware, semantic, …); inject
    via `create_app(kb_chunker=...)`.
    """

    def chunk(self, text: str) -> list[Chunk]:
        """Split `text` into ordered `Chunk`s. Each chunk's `start`/`end` MUST
        be valid char offsets into `text` (so `text[start:end]` is the verbatim
        cited span); `seq` is its 0-based position. Return `[]` for empty input.
        """
        ...


class FixedTokenChunker:
    """Fixed-size sliding window over whitespace tokens, with overlap. Token
    spans map back to char offsets so each chunk's text is a verbatim slice."""

    def __init__(self, max_tokens: int = 256, overlap_tokens: int = 32) -> None:
        self._max = max_tokens
        self._overlap = overlap_tokens

    def chunk(self, text: str) -> list[Chunk]:
        spans = [(m.start(), m.end()) for m in _TOKEN.finditer(text)]
        if not spans:
            logger.debug("chunker: empty text, 0 chunks")
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
        logger.debug("chunker: split %d tokens into %d chunks", n, len(chunks))
        return chunks
