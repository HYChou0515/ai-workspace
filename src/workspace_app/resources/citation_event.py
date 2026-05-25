"""CitationEvent — an append-only log of every persisted ``[n]`` citation.

The three "cited" counts (collection / doc / chunk) are aggregates over this
log, NOT stored counters — so they're recomputable and auditable. The counting
rule (see `kb/cited.py`) is per event: +1 to the doc and collection, +1 to EACH
source chunk; therefore ``doc_cited != sum(chunk_cited)`` (one doc citation can
credit several merged, overlapping chunks).
"""

from __future__ import annotations

from msgspec import Struct


class CitationEvent(Struct):  # → resource "citation-event" (append-only)
    collection_id: str
    document_id: str  # opaque SourceDoc id
    source_chunk_ids: list[str]  # the chunks this [n]'s merged passage spanned
    origin_kind: str  # "kb_chat" | "rca"
    origin_id: str  # kb chat id  OR  investigation id
    cited_by: str  # the asker's user id
    marker: int  # the [n] in the answer
    created_at: int | None = None  # epoch ms
