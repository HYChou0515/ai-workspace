"""Neighbouring context (plan-rag-context P2) — widen each retrieved passage to
at least N characters before and after the hit, taken in WHOLE chunks, walking
into the adjacent documents (document-tree order) when the document runs out.

Why this exists: vector retrieval matches the chunk that *phrases* the question;
the supporting detail next to it is written in different vocabulary, so it never
ranks — by construction, not by tuning. The model reading the matched chunk
sees a coherent fragment and does not know it is missing anything. So the
neighbours are added unconditionally, before the reranker (which must rank
what will actually be delivered).

Pure, like `merge.py`: the chunk boundaries, the canonical texts and the
tree-order neighbours are injected. The retriever owns the queries — bound to
the SAME scope as every retrieval arm, so an unreadable neighbour is simply
never handed to this module.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import msgspec

from ..resources.kb import RetrievedPassage


@dataclass(frozen=True)
class ChunkSpan:
    """A chunk's boundaries within its document — no text, no vector."""

    chunk_id: str
    doc_id: str
    seq: int
    start: int
    end: int


#: ``doc_id`` → its chunks, ascending by ``seq``. Only ever asked for documents
#: the caller has already deemed readable.
ChunksOf = Callable[[str], Sequence[ChunkSpan]]
#: ``doc_id`` → canonical text (the text chunk offsets index into).
TextOf = Callable[[str], str]
#: ``doc_id`` → ``(previous, next)`` document in tree order, each ``None`` at
#: the edge of the collection. Unreadable documents are already skipped.
Neighbours = Callable[[str], tuple[str | None, str | None]]
#: ``doc_id`` → the name shown on a file-boundary line inside the context.
LabelOf = Callable[[str], str]

#: How many documents the walk may cross per side. The real bound is the
#: collection edge (`neighbours` returns None); this is the structural one so
#: a cyclic `neighbours` truncates instead of hanging — and at any sane N a
#: walk that crosses this many documents has stopped meaning "context".
_MAX_DOCS_PER_SIDE = 32


def expand_passages(
    passages: Sequence[RetrievedPassage],
    *,
    min_chars: int,
    chunks_of: ChunksOf,
    text_of: TextOf,
    neighbours: Neighbours,
    label_of: LabelOf,
) -> list[RetrievedPassage]:
    """Return the passages with ``context_text`` (and the same-document
    ``context_start`` / ``context_end``) filled in, in the order given.
    ``min_chars <= 0`` is OFF: the passages come back exactly as given.

    Two hits in one document may end up with overlapping contexts (the same
    neighbouring text delivered twice). They are deliberately NOT merged: a
    merge would have to widen the HIT span to the union — thousands of chars
    that never matched, under one ``[n]`` whose snippet and highlight the user
    then cannot trust — and "the citation stays the hit" is the invariant this
    whole layer promises. Duplicated context costs tokens; a widened citation
    costs trust."""
    if min_chars <= 0:
        return list(passages)
    return [_expand_one(p, min_chars, chunks_of, text_of, neighbours, label_of) for p in passages]


def _expand_one(
    p: RetrievedPassage,
    min_chars: int,
    chunks_of: ChunksOf,
    text_of: TextOf,
    neighbours: Neighbours,
    label_of: LabelOf,
) -> RetrievedPassage:
    spans = list(chunks_of(p.document_id))
    # The hit chunks are named by id. Overlap with the hit span would NOT do:
    # a sliding-window chunker overlaps every neighbour by a few chars, so the
    # chunks on either side would count as "hit" and there would be nothing
    # left to expand into.
    hit_ids = set(p.source_chunk_ids)
    hit = [i for i, s in enumerate(spans) if s.chunk_id in hit_ids]
    if not hit:  # no chunk boundaries to snap to (a legacy row) — leave it alone
        return p
    first, last = hit[0], hit[-1]

    # Backwards within this document: whole chunks until `min_chars` precede the hit.
    ctx_start = p.start
    i = first - 1
    while p.start - ctx_start < min_chars and i >= 0:
        ctx_start = min(ctx_start, spans[i].start)
        i -= 1
    # …then into the previous documents, taking whole chunks from each one's END.
    before = _walk(
        p.document_id,
        short_by=min_chars - (p.start - ctx_start),
        step=lambda d: neighbours(d)[0],
        take=_tail,
        chunks_of=chunks_of,
        text_of=text_of,
    )

    # Forwards: symmetric, taking whole chunks from each next document's START.
    ctx_end = p.end
    i = last + 1
    while ctx_end - p.end < min_chars and i < len(spans):
        ctx_end = max(ctx_end, spans[i].end)
        i += 1
    after = _walk(
        p.document_id,
        short_by=min_chars - (ctx_end - p.end),
        step=lambda d: neighbours(d)[1],
        take=_head,
        chunks_of=chunks_of,
        text_of=text_of,
    )

    own = (p.document_id, text_of(p.document_id)[ctx_start:ctx_end])
    pieces = [*reversed(before), own, *after]
    # Every piece from ANOTHER document is labelled — including the first one.
    # The passage is rendered under the hit document's own header, so an
    # unlabelled first piece from the previous file would read as the hit
    # file's text and be cited as such. The hit's own piece is labelled only
    # when something precedes it (a lone own piece needs no label).
    parts: list[str] = []
    for i, (doc, text) in enumerate(pieces):
        if doc != p.document_id or i > 0:
            parts.append(f"── {label_of(doc)} ──\n\n{text}")
        else:
            parts.append(text)
    context = "\n\n".join(parts)
    return msgspec.structs.replace(
        p, context_text=context, context_start=ctx_start, context_end=ctx_end
    )


def _walk(
    start_doc: str,
    *,
    short_by: int,
    step: Callable[[str], str | None],
    take: Callable[[Sequence[ChunkSpan], int], tuple[int, int] | None],
    chunks_of: ChunksOf,
    text_of: TextOf,
) -> list[tuple[str, str]]:
    """Cross documents in one direction until `short_by` chars are gathered (or
    the collection ends). Returns ``(doc_id, text)`` pieces in walking order —
    nearest document first."""
    pieces: list[tuple[str, str]] = []
    doc = start_doc
    for _ in range(_MAX_DOCS_PER_SIDE):
        if short_by <= 0:
            break
        nxt = step(doc)
        if nxt is None:
            break
        doc = nxt
        rng = take(chunks_of(doc), short_by)
        if rng is None:  # a document with no chunks — walk past it
            continue
        lo, hi = rng
        pieces.append((doc, text_of(doc)[lo:hi]))
        short_by -= hi - lo
    return pieces


def _tail(spans: Sequence[ChunkSpan], want: int) -> tuple[int, int] | None:
    """Whole chunks from the END of a document, until `want` chars are covered."""
    if not spans:
        return None
    hi = spans[-1].end
    lo = hi
    j = len(spans) - 1
    while hi - lo < want and j >= 0:
        lo = min(lo, spans[j].start)
        j -= 1
    return lo, hi


def _head(spans: Sequence[ChunkSpan], want: int) -> tuple[int, int] | None:
    """Whole chunks from the START of a document, until `want` chars are covered."""
    if not spans:
        return None
    lo = spans[0].start
    hi = lo
    j = 0
    while hi - lo < want and j < len(spans):
        hi = max(hi, spans[j].end)
        j += 1
    return lo, hi
