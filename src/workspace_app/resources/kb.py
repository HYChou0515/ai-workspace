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

from .conversation import Citation as Citation  # re-export — see conversation.Citation
from .conversation import MessageMetrics

# Embedding dimensionality. MUST match the active Embedder; changing the model
# is a re-index event (every chunk re-embedded). Set per deployment.
#
# Resolution at import time:
# 1. `KB_EMBED_DIM` env explicit → use it (unfamiliar model + you know its dim)
# 2. `KB_EMBED_MODEL` env in the known table → derive (one-knob config)
# 3. Neither + model unknown → raise (silent default would corrupt the column)
# 4. Neither + bge-m3 / empty (offline) → 1024
#
# The `DocChunk.embedding` Vector column's width is bound below at class
# definition time. Changing it post-deploy means re-indexing.

_KNOWN_EMBED_DIMS: dict[str, int] = {
    "ollama/bge-m3": 1024,
    "ollama/nomic-embed-text": 768,
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
    "openai/text-embedding-ada-002": 1536,
    # Empty = offline HashEmbedder (factories.get_embedder); pin to bge-m3
    # default so existing offline deploys are unaffected.
    "": 1024,
}

_KNOWN_CODE_EMBED_DIMS: dict[str, int] = {
    "ollama/nomic-embed-text": 768,
    "ollama/nomic-embed-code": 768,
    "ollama/jina-embeddings-v2-base-code": 768,
    "": 768,
}


def _resolve_dim(
    *,
    dim_env: str,
    model_env: str,
    table: dict[str, int],
    default_model: str,
) -> int:
    """Pick an embedding dim from env. Explicit `dim_env` wins; else
    look up `model_env` (default `default_model`) in `table`; else raise
    with the offending model name so the operator knows to set the
    explicit dim."""
    explicit = os.environ.get(dim_env)
    if explicit:
        return int(explicit)
    model = os.environ.get(model_env, default_model)
    if model in table:
        return table[model]
    raise ValueError(
        f"unknown embed model {model!r} (from {model_env}); "
        f"either set {dim_env} explicitly to the model's output "
        f"width, or use one of: {sorted(table)}"
    )


def _resolve_embed_dim() -> int:
    return _resolve_dim(
        dim_env="KB_EMBED_DIM",
        model_env="KB_EMBED_MODEL",
        table=_KNOWN_EMBED_DIMS,
        default_model="ollama/bge-m3",
    )


def _resolve_code_embed_dim() -> int:
    return _resolve_dim(
        dim_env="KB_CODE_EMBED_DIM",
        model_env="KB_CODE_EMBED_MODEL",
        table=_KNOWN_CODE_EMBED_DIMS,
        default_model="",
    )


EMBED_DIM = _resolve_embed_dim()  # e.g. bge-m3 = 1024
# P3.0 code-specialised embedding width. Stored on `DocChunk.embedding_alt`
# for Collections with ``embedder_id=1`` so the retriever can fan out across
# both fields in parallel and RRF the results. Defaults to 768.
CODE_EMBED_DIM = _resolve_code_embed_dim()


# ───────────────────────────── resources ─────────────────────────────


class Collection(Struct):  # → resource "collection"
    """A named knowledge base. Each SourceDoc / DocChunk belongs to one.
    created/updated time + created_by come from specstar meta (``.info``).

    Code-repo fields (``git_*``) are P3.0 — set on a Collection bound to a
    source tree; ``CodeRepoIngestor.sync`` clones the remote, ingests each
    code/markdown file, and records ``git_last_sha`` / ``git_last_pulled_at``
    so subsequent syncs can short-circuit (and the FE can display "synced at
    commit …"). ``git_token`` is the PAT for self-hosted remotes; v1 stores
    it in plaintext under the deploy.md "DB lives in a controlled network"
    assumption — re-encrypt before SaaS.
    """

    name: str
    description: str = ""
    icon: str = "layers"  # icon name (FE Icon set) for the collection card
    git_url: str | None = None  # `https://gitlab.…/g/r.git` or `file://…` (tests)
    git_branch: str | None = None  # None ⇒ remote default
    git_token: str | None = None  # PAT for self-hosted gitlab (HTTP basic; v1 plaintext)
    git_last_sha: str | None = None  # HEAD captured at last successful sync
    git_last_pulled_at: int | None = None  # epoch ms; sweeper uses this + interval
    sync_interval_hours: int | None = None  # None ⇒ manual-sync only
    # 0 = default (text) embedder, vectors land on DocChunk.embedding.
    # 1 = code embedder, vectors land on DocChunk.embedding_alt instead so
    # the retriever can fan out across both fields in parallel + RRF.
    embedder_id: int = 0
    # Issue #50: which retrieval pipeline(s) this collection uses — two
    # independent toggles. `use_rag` = the chunk-RAG path (default on, so
    # every existing collection keeps working). `use_wiki` = the parallel
    # LLM-wiki path (maintainer builds it on ingest; reader navigates it at
    # query). Both on ⇒ both run and their answers merge.
    use_rag: bool = True
    use_wiki: bool = False
    # Issue #90: per-collection wiki guidance, APPENDED onto the bundled wiki
    # prompts (never a replacement — the machinery stays). `maintainer` shapes
    # how pages are written/organised (fold + unfold); `reader` shapes how the
    # wiki answers. Blank ⇒ the bundled prompt verbatim. Non-indexed: never
    # filtered/sorted on, so adding them needs no migration (old rows decode
    # with the empty default). See kb/wiki/guidance.with_collection_guidance.
    wiki_maintainer_guidance: str = ""
    wiki_reader_guidance: str = ""


class WikiPage(Struct):  # → resource "wiki-page"
    """One page of a collection's LLM wiki (issue #50) — markdown the wiki
    agents own and maintain. Backed by the FileStore-protocol
    ``WikiFileStore`` (one resource per page → editing a page is O(page),
    not O(whole wiki); writes use draft ``modify()`` so high-churn machine
    edits don't bloat revision history). Metadata (created/updated time,
    revision) is specstar's — not redefined here.

    Resource id = ``{collection_id}/{path}`` slash-free (see
    ``kb/wiki/store.py``). ``content`` is a Binary blob (markdown bytes)."""

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    path: str  # e.g. "/index.md", "/entities/reflow-zone-3.md"
    content: Binary


class WikiBuildState(Struct):  # → resource "wiki-build-state"
    """Live progress of a collection's wiki maintenance (#59), for the FE's
    "Updating…" UI — the durable, cross-pod replacement for the old in-memory
    status dict. One resource per collection (resource id = collection id).

    ``total`` is the count of sources enqueued in the current build epoch
    (reset when a fresh batch starts); ``current`` / ``phase`` are the live
    activity the consuming pod writes as the maintainer works; ``errors`` /
    ``last_error`` surface terminal run failures so a maintainer that writes
    nothing is never silent. ``building`` and the done-count are NOT stored —
    they're derived at read time from the live count of PENDING/PROCESSING
    jobs, so they stay correct across retries and multiple pods."""

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    total: int = 0
    current: str | None = None
    phase: str | None = None
    errors: int = 0
    last_error: str | None = None


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
    # Issue #39: a browser-displayable derivative a parser handed back
    # via `on_preview` — e.g. PptxParser's soffice-converted PDF. Its
    # own blob (separate file_id); the doc viewer iframes
    # `/blobs/{preview.file_id}` instead of a binary-download notice.
    # None for types that already display natively.
    preview: Binary | None = None
    text: str | None = None
    # Indexing lifecycle: created "indexing", flips to "ready" once its chunks
    # are embedded (slow — runs off the upload request), or "error" on failure.
    status: str = "ready"
    # Issue #39 / Q11: a short progress / error string that long-running
    # parsers (VLM image, VLM slide) write via the Ingestor's
    # `on_progress` callback so the FE polling the doc row sees
    # "VlmImageParser: page 12/50" instead of just "indexing" for 25
    # minutes. Cleared on success; carries the exception summary when
    # status flips to "error".
    status_detail: str = ""


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
    # Issue #39 / Q8c: the IParser subclass that produced this chunk
    # ("PdfParser" / "VlmImageParser" / a custom in-house parser's
    # class name). Empty string = legacy / non-parser path (the
    # canonical-text chunker / chat-pipeline insight nodes). Lets the
    # operator selectively reindex a single parser's output, or the
    # FE label chunks by their source.
    parser_id: str = ""
    # P3.0: exactly one of `embedding` / `embedding_alt` is populated per
    # chunk — `embedder_id == 0` chunks use `embedding` (default text model),
    # `embedder_id != 0` chunks use `embedding_alt` (code-specialised model).
    # Both are nullable so the retriever can fan out across both fields in
    # parallel and RRF the results.
    embedding: Annotated[list[float] | None, Vector(dim=EMBED_DIM, distance="cosine")] = None
    embedding_alt: Annotated[list[float] | None, Vector(dim=CODE_EMBED_DIM, distance="cosine")] = (
        None
    )


class IndexRun(Struct):  # → resource "index-run"
    """Issue #227: the fan-out **join state** for one indexing of a SourceDoc.

    A large index (a 100-page VLM PDF, a 100k-row CSV) is split into many small
    ``IndexJob(kind="process")`` jobs so none exceeds the broker's consumer-ack
    timeout. This row is how the independent process jobs agree on "the whole
    doc is done": the split job seeds ``total`` (number of unit batches); each
    process job idempotently records its batch index in ``done`` (or ``failed``);
    the finalize step runs exactly once, gated by the CAS-claimed ``finalized``
    flag (set only when ``done ∪ failed`` covers every batch).

    The resource id IS the doc id — one run row per doc, so a fresh fan-out
    overwrites the prior terminal run, and ``status == "running"`` is the
    queue-agnostic "an index is already in flight for this doc" guard (the
    coalescing that ``partition_key`` was meant to give but the RabbitMQ backend
    does not honor). Correctness rests on compare-and-swap, never on the queue.
    """

    doc_id: Annotated[str, Ref("source-doc", on_delete=OnDelete.cascade)]
    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    total: int
    done: list[int] = field(default_factory=list)  # batch indices that finished OK
    failed: list[int] = field(default_factory=list)  # batch indices that gave up
    finalized: bool = False  # the exactly-once finalize gate (CAS-claimed)
    status: str = "running"  # running | done | error
    # #248: a real progress aggregate for the FE bar. `units_total` is the doc's
    # unit count (e.g. PDF pages) seeded at fan-out; `units_done` is the sum of
    # completed batches' unit counts, bumped once per batch under the same CAS as
    # `done` — so it only ever climbs (parallel batches finishing out of order
    # can't make it go backward, unlike the old per-page status_detail string).
    units_total: int = 0
    units_done: int = 0


class IndexUnitText(Struct):  # → resource "index-unit-text"
    """Issue #227 fan-out **staging**: one process job's clean pre-chunk text for
    its unit batch, id ``{doc_id}.t{batch_index}``. The finalize step rejoins
    these in batch order into ``SourceDoc.text`` (the wiki maintainer / citation
    canonical text) and then deletes them — transient, alive only between a
    fan-out's process jobs and its finalize."""

    doc_id: Annotated[str, Ref("source-doc", on_delete=OnDelete.cascade)]
    batch_index: int
    text: str


class ContextCard(Struct):  # → resource "context-card"
    """Issue #106: a lightweight glossary card — several ``keys`` (a term and
    its surface forms) → a short ``body`` explanation, looked up deterministically
    by exact key membership. No embedding, no chunking: the cheap path alongside
    SourceDoc/DocChunk.

    key ↔ card is many-to-many — one card carries several ``keys``; one key may
    resolve to several cards. ``norm_keys`` is the DERIVED, indexed lookup
    surface (``kb.context_cards.derive_norm_keys``): server-owned, materialised on
    write by the create/update custom actions, never hand-edited. Callers query
    ``QB["norm_keys"].contains(norm(q))`` — exact element membership, the same
    index path as ``KbChat.shared_with``.
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    keys: list[str]  # author surface forms: ["M4", "Metal 4", "capping"]
    norm_keys: list[str] = field(default_factory=list)  # derived + indexed; server-set
    title: str = ""  # display name (FE list/detail); "" → keys[0]
    body: str = ""  # markdown explanation


# ─────────────────── value structs (nested / payloads) ───────────────────


class KbMessage(Struct):
    """One message in a KB chat thread. Like the RCA Message + citations on
    assistant answers. ``created_at`` kept: it's a sub-object of KbChat, so
    specstar doesn't track per-message timestamps."""

    role: str  # user / assistant / tool / error
    content: str = ""
    reasoning: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    citations: list[Citation] = field(default_factory=list)
    created_at: int | None = None  # epoch ms
    metrics: MessageMetrics | None = None  # assistant answers: final token usage (survives reload)
    error_kind: str | None = None  # role=error (#37): error | cancelled | max_turns
    stopped_reason: str | None = None  # #113: "repetition" → truncated + FE notice on reload


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
