"""Exact-string search over the knowledge base (plan-rag-context P3) — the
pure half. The Ctrl+F arm: it locates, it does not rank.

Why a separate arm: neither retrieval arm can find "Fig. 1". Dense retrieval
sees no semantic similarity between the reference and a caption; BM25 tokenizes
on ``\\w+`` so the query becomes ``["fig", "1"]`` — two terms with zero
discrimination and no phrase concept — and the trigram pre-narrowing in front
of it is fuzzy by design. Part numbers, error codes, section numbers are the
same hole.

Completeness across chunk boundaries: the store is pre-narrowed on ONE token of
the pattern (the longest — the most selective), never the whole phrase. A phrase
longer than the chunker's overlap can straddle two chunks, so ``icontains(whole
phrase)`` would miss it; any occurrence's longest token, though, lies whole
inside some chunk, and the exact match is then verified on the canonical text
with the chunk's span widened by the pattern's length on each side.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Longest line the hit carries — a match on a 40 kB single-line file must not
#: ship the whole file (same cap as `api.search.search_text`).
MAX_LINE_LEN = 400


@dataclass(frozen=True)
class GrepHit:
    """One matching LINE of one document. Not a passage, not citable — a
    location the agent reads from (`read_lines` / `read_page`)."""

    document_id: str
    path: str
    filename: str
    line: int  # 1-based
    text: str
    page: int | None  # the containing chunk's page, when the parser knew one


@dataclass(frozen=True)
class GrepResult:
    hits: list[GrepHit]
    total: int  # before the cap


def anchor_of(query: str) -> str:
    """The token of a literal query the store is pre-narrowed on: the longest
    whitespace-delimited run (most selective), or the whole query when it has
    no whitespace."""
    tokens = query.split()
    if not tokens:
        return query
    longest: str = max(tokens, key=len)  # ty: ignore[invalid-assignment]
    return longest


def occurrences(text: str, pattern: re.Pattern[str], lo: int, hi: int) -> list[int]:
    """Start offsets of every match of `pattern` in ``text[lo:hi]`` (absolute)."""
    lo = max(0, lo)
    hi = min(len(text), hi)
    return [m.start() for m in pattern.finditer(text, lo, hi)]


def line_at(text: str, offset: int) -> tuple[int, str]:
    """``(1-based line number, the line's text capped at MAX_LINE_LEN)`` for the
    line containing `offset`."""
    line_no = text.count("\n", 0, offset) + 1
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end == -1:
        end = len(text)
    line = text[start:end]
    if len(line) > MAX_LINE_LEN:
        line = line[:MAX_LINE_LEN] + "…"
    return line_no, line
