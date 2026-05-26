"""KB (knowledge-base chatbot) resources — a subsystem separate from RCA.

In-house docs → named collections → chunked + embedded → queried by the KB agent.
specstar provides: vector store (RAW vectors + cosine), Binary blobs (content
addressed: ``content.file_id`` is the xxh3 hash, content_type auto-sniffed via
magic), CRUD, audit meta (``.info``), revision history (recovery), and custom
resource ids. Embedding is done by our swappable Embedder (query/doc instruction
prefixes), stored RAW — specstar does no auto-encoding here.

Identity: SourceDoc is created with a NATURAL-KEY resource id
(``{collection_id}/{created_by}/{path}``) percent-encoded slash-free (specstar
ids can't contain ``/``; see ``kb.doc_id``), so cross-user same-name uploads
don't clobber, and re-uploading the same logical doc becomes a new revision (the
old one stays recoverable). See the grill design for the full rationale.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from msgspec import Struct, field
from specstar import OnDelete, Ref, Vector
from specstar.types import Binary

# Embedding dimensionality. MUST match the active Embedder; changing the model
# is a re-index event (every chunk re-embedded). Set per deployment.
EMBED_DIM = int(os.getenv("KB_EMBED_DIM", "1024"))  # e.g. bge-m3 = 1024


# ───────────────────────────── resources ─────────────────────────────


class Collection(Struct):  # → resource "collection"
    """A named knowledge base. Each SourceDoc / DocChunk belongs to one.
    created/updated time + created_by come from specstar meta (``.info``)."""

    name: str
    description: str = ""
    icon: str = "layers"  # icon name (FE Icon set) for the collection card


class SourceDoc(Struct):  # → resource "source-doc"
    """One ingested document. Its resource id is the natural key
    ``{collection_id}/{created_by}/{path}`` percent-encoded into a slash-free
    token (specstar ids can't contain ``/``); see ``kb.doc_id``.

    - ``content`` — the original uploaded bytes. ``content.file_id`` is the
      content hash (dedup / no-op-on-reupload key); ``content.content_type`` is
      auto-sniffed via magic; identical bytes are stored once by the blob store.
    - ``text`` — derived/extracted text (may be produced by an LLM/OCR later,
      hence NOT the identity key). ``None`` ⇒ decode ``content`` (md/txt).
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    path: str  # relative path within the upload; part of the id + cross-ref key
    content: Binary
    text: str | None = None
    # Indexing lifecycle: created "indexing", flips to "ready" once its chunks
    # are embedded (slow — runs off the upload request), or "error" on failure.
    status: str = "ready"


class DocChunk(Struct):  # → resource "doc-chunk"
    """A retrievable slice of a SourceDoc — DERIVED and current-only (deleted +
    rebuilt when the SourceDoc changes; on rollback you re-index).

    - ``start``/``end`` — char span into the doc's canonical text (the verbatim
      source region, for citation highlight).
    - ``text`` — the representation we embed/retrieve; the Chunker may fold in
      structural context (e.g. a heading breadcrumb), so it need not equal
      ``canonical_text[start:end]``.
    - ``embedding`` — computed by our Embedder, stored RAW.
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    source_doc_id: Annotated[str, Ref("source-doc", on_delete=OnDelete.cascade)]
    seq: int  # 0-based order within the doc (adjacency merge)
    start: int  # inclusive char offset into canonical text
    end: int  # exclusive char offset
    text: str
    embedding: Annotated[list[float], Vector(dim=EMBED_DIM, distance="cosine")]


# ─────────────────── value structs (nested / payloads) ───────────────────


class Citation(Struct):
    """A parsed ``[n]`` marker in a KB answer, resolved to its source. Retrieved
    chunks get MERGED, so chunk-level provenance is the SET of original chunk
    ids that composed the cited passage."""

    marker: int  # the [n] in the answer
    collection_id: str
    document_id: str  # SourceDoc resource id (encoded natural key; see kb.doc_id)
    filename: str  # display name = basename(path)
    start: int  # merged span (min start) into canonical text
    end: int  # max end
    source_chunk_ids: list[str]  # original DocChunk ids merged
    snippet: str = ""


class KbMessage(Struct):
    """One message in a KB chat thread. Like the RCA Message + citations on
    assistant answers. ``created_at`` kept: it's a sub-object of KbChat, so
    specstar doesn't track per-message timestamps."""

    role: str  # user / assistant / tool
    content: str = ""
    reasoning: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    citations: list[Citation] = field(default_factory=list)
    created_at: int | None = None  # epoch ms


class KbChat(Struct):  # → resource "kb-chat"
    """A persistent chat thread against one or more collections. created/updated
    time come from specstar meta (``.info.updated_time`` → recency sort).

    Private by default (owner = ``created_by`` meta). The owner can share it
    read-only with other users via ``shared_with``; those users see it under
    "Shared with me" and can read it, but only the owner can send."""

    title: str = "New chat"
    collection_ids: list[str] = field(default_factory=list)  # plain ids
    messages: list[KbMessage] = field(default_factory=list)
    shared_with: list[str] = field(default_factory=list)  # user ids (read-only)


class RetrievedPassage(Struct, frozen=True):
    """A MERGED passage from the Retriever — mode (b) result, and the unit the
    LLM cites in mode (a). ``start``/``end`` span the merged region of the
    canonical text."""

    collection_id: str
    document_id: str
    filename: str  # display = basename(path)
    start: int
    end: int
    source_chunk_ids: list[str]
    text: str
    score: float = 0.0
