"""Text-merge (parent-document retrieval) — combine retrieved chunks that come
from the same document and overlap/abut into a single coherent passage, so the
LLM sees whole regions rather than fragments and citations point at one span.

Pure: the canonical text is injected via `text_of`. Returns RetrievedPassage in
descending score order.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from msgspec import Struct

from ..resources.kb import RetrievedPassage


class ScoredChunk(Struct, frozen=True):
    """A retrieved chunk plus its fused score — the input to merging."""

    chunk_id: str
    document_id: str
    collection_id: str
    filename: str
    seq: int
    start: int
    end: int
    score: float


def merge_passages(
    chunks: Sequence[ScoredChunk], *, text_of: Callable[[str], str]
) -> list[RetrievedPassage]:
    by_doc: dict[str, list[ScoredChunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.document_id, []).append(c)

    passages: list[RetrievedPassage] = []
    for doc_id, group in by_doc.items():
        group.sort(key=lambda c: c.start)
        run: list[ScoredChunk] = []
        for c in group:
            if run and c.start <= run[-1].end:  # overlaps/abuts the current run
                run.append(c)
            else:
                if run:
                    passages.append(_passage(run, text_of))
                run = [c]
        passages.append(_passage(run, text_of))
    passages.sort(key=lambda p: p.score, reverse=True)
    return passages


def _passage(run: list[ScoredChunk], text_of: Callable[[str], str]) -> RetrievedPassage:
    start = min(c.start for c in run)
    end = max(c.end for c in run)
    doc_id = run[0].document_id
    return RetrievedPassage(
        collection_id=run[0].collection_id,
        document_id=doc_id,
        filename=run[0].filename,
        start=start,
        end=end,
        source_chunk_ids=[c.chunk_id for c in sorted(run, key=lambda c: c.seq)],
        text=text_of(doc_id)[start:end],
        score=max(c.score for c in run),
    )
