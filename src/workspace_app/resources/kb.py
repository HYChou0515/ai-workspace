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

from ..perm import Permission
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

# #513: image-embedding width. Stored on `DocChunk.embedding_img` — its OWN
# space, separate from EMBED_DIM / CODE_EMBED_DIM. No image model is wired yet
# (it is an external deliverable); "" (offline / the HashImageEmbedder stub)
# pins to a CLIP ViT-B width so the column has a fixed size. When the real model
# lands, set KB_IMG_EMBED_DIM (or KB_IMG_EMBED_MODEL) to its output width.
_KNOWN_IMG_EMBED_DIMS: dict[str, int] = {
    "openai/clip-vit-base-patch32": 512,
    "openai/clip-vit-large-patch14": 768,
    "": 512,
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


def _resolve_img_embed_dim() -> int:
    return _resolve_dim(
        dim_env="KB_IMG_EMBED_DIM",
        model_env="KB_IMG_EMBED_MODEL",
        table=_KNOWN_IMG_EMBED_DIMS,
        default_model="",
    )


EMBED_DIM = _resolve_embed_dim()  # e.g. bge-m3 = 1024
# P3.0 code-specialised embedding width. Stored on `DocChunk.embedding_alt`
# for Collections with ``embedder_id=1`` so the retriever can fan out across
# both fields in parallel and RRF the results. Defaults to 768.
CODE_EMBED_DIM = _resolve_code_embed_dim()
# #513 image-embedding width. Stored on `DocChunk.embedding_img`, an additive
# third retrieval signal beside the text/code vectors. Defaults to 512.
IMG_EMBED_DIM = _resolve_img_embed_dim()


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
    # #262: per-resource access control. None ≡ public (back-compat; no
    # migration). `permission.read_meta` + `permission.visibility` are indexed so
    # the collection list can be filtered to what the caller may see.
    permission: Permission | None = None
    # Issue #308: count of this collection's docs carrying a per-doc read override
    # (SourceDoc.permission). A cheap short-circuit for the AI-retrieval denylist:
    # when 0, the retrieval path skips the "which docs is the speaker blocked from"
    # query entirely, so a collection nobody has tightened per-doc pays nothing.
    # Maintained (+1 on set / −1 on clear) by the doc-permission endpoints; never
    # filtered/sorted on, so it's NON-indexed → adding it needs no migration (old
    # rows decode with the 0 default = "no overrides").
    has_doc_overrides: int = 0
    # Issue #105: the user-authored quality rubric — what makes a doc a good/bad
    # knowledge source for THIS collection, and which named dimensions to assess.
    # The judge composes it with a system-fixed output format (overall 0–100 +
    # per-dimension breakdown + rationale) at index time. Blank ⇒ the collection
    # is NOT scored (opt-in) and its search ranking is unaffected. Non-indexed
    # (never filtered/sorted on), like the wiki guidance above — adding it needs
    # no migration (old rows decode with the empty default).
    quality_rubric: str = ""
    # Issue #328: per-collection config for prompt/param-driven parsers,
    # keyed by parser id (`type(parser).__name__`, the same key on
    # `DocChunk.parser_id`) → `{knob: value}`. The default for tunable
    # extraction (e.g. an ontology parser's prompt) at THIS collection; a
    # per-doc `SourceDoc.parser_config_overrides` can still override one
    # knob. Resolved at index time by `kb.parser_config.effective_config`
    # (parser defaults < this < per-doc override). Non-indexed (never
    # filtered/sorted on), like `quality_rubric` — adding it needs no
    # migration (old rows decode with the empty default).
    parser_configs: dict[str, dict[str, Any]] = {}
    # Issue #328: a single per-collection free-text steering prompt APPENDED to
    # every prompt-driven parser's base prompt at index time (e.g. "a fishbone
    # diagram → emit JSON; a table → emit Markdown"). Unlike `parser_configs`
    # (per-parser-id structured knobs) this is one string shared across ALL the
    # collection's prompt-driven parsers (the VLM describer family — whichever a
    # given upload routes through), the 5th sibling of `quality_rubric` /
    # `wiki_*_guidance`. The findability-probe modal tunes a CANDIDATE of this on
    # ONE doc (dry-run, never persisted); "Apply" writes it here. Blank ⇒ nothing
    # appended (back-compat). Non-indexed (never filtered/sorted on) — adding it
    # needs no migration (old rows decode with the empty default).
    parser_guidance: str = ""
    # Issue #377: opt-in proactive clarification. When True, each doc is digested
    # once it's ready (the SAME card-drafting pass that proposes cards also raises
    # the terms it can't define and the passages it can't follow, as DocQuestions
    # for a human to answer). Default False ⇒ the digest is a MANUAL action only
    # (the user still triggers card generation by hand). Non-indexed (never
    # filtered/sorted on), like `quality_rubric` — adding it needs no migration
    # (old rows decode with the False default).
    auto_digest: bool = False
    # Issue #479: when the collection's prose wiki was last reflected (statistically
    # consolidated) — an ISO-8601 UTC timestamp the daily/manual reflect pass stamps
    # on completion, or "" if it has never run. Drives the FE "上次沉思" label.
    # Non-indexed (never filtered/sorted on), like the wiki guidance / rubric above —
    # adding it needs no migration (old rows decode with the empty default).
    last_reflected_at: str = ""


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
    # Issue #88: a CJK-aware token estimate of `text` (see kb.tokens), computed
    # at index time and indexed so the collection grid can SUM a chunk-based
    # "≈ N tokens" figure per collection — instead of the old raw-blob bytes/4
    # estimate that was wildly wrong for binary formats (a 10 MB PDF whose
    # extracted text is 50 KB). 0 while indexing / on error (no text yet).
    token_count: int = 0
    # Issue #105: the AI's quality assessment of this doc as a knowledge source,
    # judged against the collection's `quality_rubric`. `quality_score` is a
    # holistic 0–100 grade — INDEXED so the document list can sort by quality
    # and the retriever can batch-load candidate doc scores to down-weight bad
    # docs. `None` = UN-SCORED (the neutral default: a collection with no rubric,
    # a doc indexed before a rubric existed, or one still being judged) — it is
    # never penalised in search. `quality_breakdown` is the per-dimension scores
    # whose keys are named BY the user's rubric (so the schema varies per
    # collection → a free-form dict, non-indexed); `quality_rationale` is the
    # AI's short justification shown in the UI. Both are display-only. Scoring
    # runs async after `status="ready"` (see kb.quality / kb.index_coordinator),
    # so a judge failure leaves the doc un-scored, never un-indexed.
    quality_score: int | None = None
    quality_breakdown: dict[str, Any] = {}
    quality_rationale: str = ""
    # Issue #328: per-doc override of a prompt/param-driven parser's STRUCTURED
    # config (the param "escape hatch") — same shape as `Collection.parser_configs`
    # (parser_id → {knob: value}), and it WINS over the collection config in the
    # precedence merge (`kb.parser_config.effective_config`: parser defaults <
    # collection < per-doc). Storing it here — not just the resulting chunks —
    # makes the fix durable: a later full re-index re-parses this doc with ITS
    # override. DORMANT until a parser declares `config_fields()` AND a UI writes
    # this — no concrete parser does either today, so it currently resolves to {}
    # for every parser. The FREE-TEXT per-doc escape hatch the Tune-parsing modal
    # actually writes is `parser_guidance_override` below (#356), NOT this.
    # Non-indexed → no migration (old rows decode with the empty default).
    parser_config_overrides: dict[str, dict[str, Any]] = {}
    # Issue #356: the per-doc FREE-TEXT escape hatch — a non-empty value REPLACES
    # the collection's `parser_guidance` for THIS doc at index time (see
    # `Ingestor._parse_guidance_for`), so a few special docs can opt out of the
    # collection prompt without changing it for everyone. Empty (default) ⇒ the
    # doc inherits the collection guidance (today's behaviour for every doc). The
    # Tune-parsing modal's "Save for this document" writes it; "Clear document
    # override" resets it to "". Carried forward across a re-upload (see
    # `_store_source_doc`). Non-indexed → no migration (old rows decode empty).
    parser_guidance_override: str = ""
    # Issue #303: a DENORMALIZED mirror of the parent collection's access-control
    # fields, so a doc inherits the collection's READ visibility at the storage
    # layer — the `source_doc` access_scope (perm.scope) reads these to hide a doc
    # whose collection the caller can't see, covering even the auto-CRUD
    # `GET /source-doc/{id}` (a route-guard can't). Only the fields the scope needs
    # are mirrored: `visibility` + `read_meta` (existence/read_meta) + the
    # collection's `created_by` (so the collection owner sees all its docs — the
    # doc's OWN uploader is irrelevant to inheritance). `read_content` is NOT
    # mirrored: content reads (render_document / chunks / export) are route-guarded
    # against the collection's LIVE permission, so changing read_content needs no
    # fan-out. Set from the collection at doc-create; re-pushed by a fan-out job
    # when the collection's visibility/read_meta changes (see the collection
    # permission setter). Absent on pre-#303 rows ⇒ the "public" default ≡ the
    # legacy "anyone could read" behaviour, so no migration changes visibility.
    collection_visibility: str = "public"
    collection_read_meta: list[str] = []
    collection_created_by: str = ""
    # Issue #308: this doc's OWN read-access override, on TOP of the inherited
    # `collection_*` mirror above. Effective read = the collection allows AND this
    # override allows — it can only TIGHTEN (never loosen), so a doc can be hidden
    # from someone who can read the rest of the collection, but never shared with
    # someone who can't. `None` (the default, every pre-#308 row) ≡ no override ≡
    # pure inheritance. Only `visibility` + `read_meta` (existence) and
    # `read_content` are honoured in v1; other verbs persist but are inert. Set
    # only by the collection owner via `PUT /kb/documents/{id}/permission`; the
    # doc's OWN uploader gets no special right (the owner in the read decision is
    # the collection owner, `collection_created_by`). Self-contained: the
    # collection→doc mirror fan-out NEVER touches this field. `permission.visibility`
    # / `permission.read_meta` are indexed so the storage-scope + AI denylist can
    # filter on it. Absent on pre-#308 rows ⇒ `None` ⇒ no override (no migration).
    permission: Permission | None = None


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
    seq: int  # 0-based order within the doc (adjacency merge)
    start: int  # inclusive char offset into canonical text
    end: int  # exclusive char offset
    text: str
    # #104: NOT a Ref/cascade any more — a chunk is bound to CONTENT
    # (source_file_id), not to one deletable doc. Deletion is governed by a
    # collection-scoped content refcount (`ingest.teardown_doc_chunks`), not the
    # source-doc cascade, so tearing down one holder of shared content leaves the
    # chunk set for the surviving siblings (resolved by file_id — no re-home).
    # Still WRITTEN (= the owning doc id) and INDEXED as a plain string: it is the
    # legacy / coalescing fallback for pre-#104 chunks whose source_file_id == "".
    # Default "" is the field's RETIREMENT surface: once a later PR stops writing it
    # (after prod is reindexed and physically drops the field), construction no
    # longer needs it. Ordered after the required fields so msgspec accepts the
    # default (the struct is not kw_only).
    source_doc_id: str = ""
    # #104: the content hash (== SourceDoc.content.file_id) of the bytes this
    # chunk was derived from. Identical content uploaded to several paths shares
    # ONE chunk set keyed by this; retrieval / GC resolve a chunk's content
    # WITHOUT depending on a single (deletable) source_doc_id. Empty on pre-#104
    # rows (populated on the next reindex); a fresh index always stamps it.
    source_file_id: str = ""
    # Issue #39 / Q8c: the IParser subclass that produced this chunk
    # ("PdfParser" / "VlmImageParser" / a custom in-house parser's
    # class name). Empty string = legacy / non-parser path (the
    # canonical-text chunker / chat-pipeline insight nodes). Lets the
    # operator selectively reindex a single parser's output, or the
    # FE label chunks by their source.
    parser_id: str = ""
    # Issue #254: structural source location the parser knew but the bare
    # ``start``/``end`` offsets lose — ``{"page": 3, "section": "Ch.2 > 2.1"}``
    # for a PDF, ``{"sheet": "Q3"}`` for Excel, ``{"slide": 4}`` /
    # ``{"jsonl_line": 12}`` elsewhere. Collected from the splitter node's
    # metadata at emit time (see ``ingest._PROVENANCE_KEYS``); ``{}`` when the
    # parser exposed no location (graceful degrade). Threaded through retrieval
    # → merge → Citation so the LLM and the FE can say "p.3 §2.1", not an
    # opaque char span. Not embedded — see the breadcrumb fold in li_pipeline.
    provenance: dict[str, Any] = field(default_factory=dict)
    # P3.0: exactly one of `embedding` / `embedding_alt` is populated per
    # chunk — `embedder_id == 0` chunks use `embedding` (default text model),
    # `embedder_id != 0` chunks use `embedding_alt` (code-specialised model).
    # Both are nullable so the retriever can fan out across both fields in
    # parallel and RRF the results.
    embedding: Annotated[list[float] | None, Vector(dim=EMBED_DIM, distance="cosine")] = None
    embedding_alt: Annotated[list[float] | None, Vector(dim=CODE_EMBED_DIM, distance="cosine")] = (
        None
    )
    # #513: the image vector, an ADDITIVE third signal. Unlike embedding /
    # embedding_alt (exactly one set), this may coexist with `embedding` on the
    # same chunk — a defect image indexed by both its VLM description (text) and
    # its pixels. Nullable + its own IMG_EMBED_DIM space; None until an image
    # embedder is wired (create_app(kb_image_embedder=...)), so existing chunks
    # and the text-only retrieval path are untouched.
    embedding_img: Annotated[list[float] | None, Vector(dim=IMG_EMBED_DIM, distance="cosine")] = (
        None
    )


class CachedChunk(Struct):
    """#390: one chunk payload inside an :class:`IndexCache` entry — everything
    needed to rebuild a ``DocChunk`` for a different doc WITHOUT re-parsing or
    re-embedding. Mirrors the persisted ``DocChunk`` fields except identity
    (``collection_id`` / ``source_doc_id`` are stamped fresh on copy). The
    vectors are stored as plain ``list[float]`` (not a searchable ``Vector``):
    the cache is a keyed blob looked up by id, never vector-queried."""

    seq: int
    start: int
    end: int
    text: str
    parser_id: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    embedding_alt: list[float] | None = None
    embedding_img: list[float] | None = None  # #513: mirrors DocChunk.embedding_img


class IndexCache(Struct):  # → resource "index-cache"
    """#390: a reusable index result, so re-indexing content that was already
    indexed under the same settings copies the chunks instead of re-parsing +
    re-embedding (the expensive work).

    Content-addressed: the resource id IS the composite key
    ``hash(content.file_id + effective prompt + embedder identity)`` (see
    ``kb.index_cache.compute_cache_key``) — so it is SHARED across docs and
    collections (no ``Ref``: a moved/renamed doc, or the same bytes uploaded to a
    second path, resolves the same entry). A hit guarantees the reused chunks
    match what a fresh index would produce: same bytes (``content.file_id``),
    same extraction settings, same embedding space (``embedder identity``).

    Stores the whole index output: the chunk payloads (incl. raw vectors), the
    extracted ``text`` (→ ``SourceDoc.text``), and the browser ``preview`` Binary
    (regenerating it — e.g. soffice pptx→pdf — is itself expensive, so it is
    cached too). Written when a real (cache-miss) index completes; read on the
    producer path (upload / move). No GC in v1 — a superseded entry (content or
    settings changed) is simply orphaned; a later sweep can drop stale rows."""

    chunks: list[CachedChunk] = field(default_factory=list)
    text: str | None = None
    preview: Binary | None = None


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


class CodeWikiBuildRun(Struct):  # → resource "code-wiki-build-run"
    """Issue #281 (P4): the fan-out **join state** for one code-wiki build of a
    collection. Mirrors :class:`IndexRun`.

    A code-wiki build's heavy L0 work (one card per source file) is fanned out
    into many small ``code_card`` jobs (one per directory-coherent, token-capped
    batch — see ``plan_card_batches``). This row is how the independent card jobs
    agree on "every card is built": the ``code_split`` job seeds ``total`` (number
    of batches); each card job idempotently records its batch index in ``done``
    (or ``failed``); the ``code_finalize`` step (directory roll-up + architecture
    + orphan prune) runs exactly once, gated by the CAS-claimed ``finalized`` flag
    (set only when ``done ∪ failed`` covers every batch).

    The resource id IS the collection id — one run per collection, so a fresh
    build overwrites the prior terminal run, and ``status == "running"`` is the
    queue-agnostic "a build is already in flight" guard (the card jobs are
    ``partition_key=None`` for free parallelism, so coalescing can't rest on the
    queue). Correctness rests on compare-and-swap against the etag, never on the
    queue's partition_key."""

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    total: int
    done: list[int] = field(default_factory=list)  # batch indices that built OK
    failed: list[int] = field(default_factory=list)  # batch indices that gave up
    finalized: bool = False  # the exactly-once finalize gate (CAS-claimed)
    status: str = "running"  # running | done | error
    phase: str = "cards"  # cards | finalizing — coarse activity for the FE


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


class DocQuestion(Struct):  # → resource "doc-question"
    """Issue #377: a clarification question the per-doc digest raises when it can't
    confidently define a term (``kind="term"`` → a context card) or follow a passage
    (``kind="description"`` → a wiki "clarifications" page) — instead of hallucinating
    knowledge into the KB. A human answers it in the global inbox; the answer lands
    directly (trusted), and ``result_ref`` points at what it produced.

    ``term`` questions dedupe at collection level by ``norm_key`` (the same
    ``kb.context_cards.norm``): one open question per unknown term, accumulating the
    ``source_doc_ids`` that raised it, so a human answers once and it applies
    everywhere. ``description`` questions are doc-specific (``source_doc_id`` +
    ``quote``) and never dedupe.
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    kind: str  # "term" | "description"
    status: str = "open"  # "open" | "answered" | "discarded"
    question_text: str = ""  # the AI's question
    # term questions: deduped by norm_key across the collection
    term: str = ""  # author surface form (e.g. "M4")
    norm_key: str = ""  # derived (norm(term)); indexed for dedup + lookup
    source_doc_ids: list[str] = field(default_factory=list)  # docs that raised the term
    # description questions: bound to one doc + the passage it quotes
    source_doc_id: str = ""
    quote: str = ""  # the passage the AI couldn't follow
    # answer / landing
    answer: str = ""  # the human's answer (once answered)
    result_ref: str = ""  # produced card id or clarification page path (provenance)


class ClusterMember(Struct):  # → resource "cluster-member"
    """Issue #506 P6: the reconcile projection table. Card-generation candidates
    (proposals + term questions) AND a collection's existing cards are projected
    into this one flat table, each carrying a text ``embedding``, so a single
    native cosine query finds the nearest member — whether that is an existing
    card (⑥: already explained → suppress / update) or a prior run's still-pending
    candidate (⑤: cross-run duplicate → same cluster). :class:`ContextCard` itself
    stays a deterministic exact-key glossary with no vector; this table is where
    the semantic identity lives.

    ``cluster_key`` groups semantically-equal members so the review inbox can
    ``GROUP BY`` it (one row per concept, P7). It is assigned deterministically
    for an exact ``norm_key`` match and by nearest-neighbour otherwise. ``state``
    de-joins the source row's lifecycle so the inbox never has to look it up:
    ``active`` (in the queue), ``suppressed`` (auto-dropped as already-explained,
    hidden but auditable), ``inactive`` (source committed / rejected)."""

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    kind: str  # "proposal" | "term_question" | "card"
    ref_id: str  # the source row's id (ProposedCard.id / DocQuestion id / ContextCard id)
    run_id: str = ""  # kind="proposal": the CardGenRun this candidate came from
    norm_key: str = ""  # norm(term / primary key) — the exact-match fast path
    cluster_key: str = ""  # the assigned group key (== a norm_key of the group's seed)
    state: str = "active"  # active | suppressed | inactive
    reason: str = ""  # why a suppressed member was auto-dropped: "wiki" | "near-card" | ""
    # a short human label for the suppressed-audit view (the surface term/title), so
    # the audit reads without re-fetching a dropped candidate that was never persisted
    label: str = ""
    embedding: Annotated[list[float] | None, Vector(dim=EMBED_DIM, distance="cosine")] = None


# ─────────────────── value structs (nested / payloads) ───────────────────


class KbMessage(Struct):
    """One message in a KB chat thread. Like the RCA Message + citations on
    assistant answers. ``created_at`` kept: it's a sub-object of KbChat, so
    specstar doesn't track per-message timestamps."""

    role: str  # user / assistant / tool / error
    content: str = ""
    # #242: the sender's user id (mirrors RCA `Message.author`) — stamped
    # server-side on user messages so a shared thread tells who said what and
    # the LLM history projection can attribute each message to its author.
    author: str | None = None
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

    title: str = ""  # #357: "" = unnamed → FE labels it by name_hint (first msg)
    collection_ids: list[str] = field(default_factory=list)  # plain ids
    messages: list[KbMessage] = field(default_factory=list)
    # #304: LEGACY read-only share list. Superseded by `permission` (below) — new
    # shares write `permission.read_chat`; the migration backfills `permission`
    # from this and clears it. Kept only so pre-#304 rows still decode + stay
    # readable by their shared users via the access_scope's fallback until an
    # operator runs `POST /kb-chat/migrate/execute`.
    shared_with: list[str] = field(default_factory=list)  # user ids (read-only)
    # #304: access control — the SAME embedded `Permission` that governs
    # collections / WorkItems, but KbChat INVERTS the default: absent ≡ PRIVATE
    # (owner-only), not public (a chat is not open to everyone). `read_chat` = can
    # read the thread; `converse` = can send. Set at create (private) + via the
    # per-chat permission endpoint; enforced by `kbchat_access_scope` + the write
    # checker. `None` only on un-migrated legacy rows (owner + `shared_with` fallback).
    permission: Permission | None = None


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
    # Issue #254: the merged passage's aggregated source location
    # (``{"page": [3, 4], "section": ["Ch.2 > 2.1"]}``) — distinct values in
    # seq order across the merged chunks. ``{}`` when no chunk had provenance.
    provenance: dict[str, Any] = field(default_factory=dict)
