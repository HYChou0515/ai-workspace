"""Retriever — hybrid retrieval over a set of collections.

Pipeline: dense (specstar's native vector query over the stored chunk vectors —
pgvector-indexed when available) + sparse (BM25) → Reciprocal Rank Fusion → MMR
diversification → parent-document merge → top-k RetrievedPassage. The Embedder
is injected (asymmetric query embedding); the LLM-driven enhancements
(multi-query, HyDE, rerank) layer on top via an optional `llm`.

The normal path never loads the whole collection: the dense ranking is pushed into
the store (pgvector), the sparse (BM25) corpus is pre-narrowed to the chunks
trigram-similar to a query term (`text`'s pg_trgm index, via `.fuzzy`), and only
the fused candidates' chunks are hydrated (metadata + vector) for MMR / quality /
passage build. Cosine is the shared dense metric, so the dense order matches the
stored vectors' geometry. The #328 overlay path is the sole exception — it rebuilds
the candidate set in memory and so loads every chunk with its vector.
"""

from __future__ import annotations

import logging
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from specstar import QB, SpecStar
from specstar.query import ConditionBuilder
from specstar.types import ResourceIDNotFoundError
from specstar.util.vector_distance import cosine_distance

from ..config.schema import EnhancementSettings
from ..resources.kb import DocChunk, RetrievedPassage, SourceDoc
from .bm25 import bm25_rank, tokenize
from .embedder import Embedder
from .fusion import mmr, rrf_scores
from .image_embedder import ImageEmbedder
from .ingest import normalize_text
from .llm import ILlm, OnChunk
from .merge import ScoredChunk, merge_passages
from .query import expand_queries, hypothetical_document
from .rerank import rerank_passages

logger = logging.getLogger(__name__)

# Shown in place of a legacy binary doc that has no extracted text — so the
# LLM (and the user) see a clear "nothing to read here" note instead of a
# wall of U+FFFD replacement characters (#114).
_NO_EXTRACTABLE_TEXT = "(no extractable text; this file has only raw binary content)"


@dataclass(frozen=True)
class Enhancements:
    """Per-search override for the LLM-driven enhancements. Any field
    set to `None` (the default) inherits from the operator's
    `EnhancementSettings.<knob>.default`. The operator's `max` is the
    final word — caller-set values are clamped, LLM-set tool args even
    more so.

    `expand` and `hyde` are integer counts: `0` disables; positive
    values request that many alt queries / hypothetical docs. `rerank`
    is a bool — `False` skips the rerank LLM call.
    """

    expand: int | None = None
    hyde: int | None = None
    rerank: bool | None = None


@dataclass(frozen=True)
class Overlay:
    """#328: a dry-run candidate-set override for ONE document. The retriever
    runs its NORMAL hybrid pipeline but with ``shadow_doc_id``'s stored chunks
    REMOVED and ``virtual_chunks`` (freshly re-parsed/re-embedded for a candidate
    parser config, NOT persisted) ADDED — so the findability modal can preview
    "if this doc were parsed with a different prompt, would the answer be
    retrievable?" without touching the index.

    Every ranking stage that already reads the in-memory chunk set (BM25 corpus,
    MMR similarity, quality prior, parent-merge, rerank) sees the virtual chunks
    through the SAME code — so a later change to e.g. the rerank algorithm flows
    into the preview automatically, no parallel implementation to drift. The only
    preview-specific step is the dense order, recomputed in-memory with the
    shared cosine metric (identical geometry to the stored-vector query).

    ``virtual_text`` is the re-parsed document's canonical text that the virtual
    chunks' ``start``/``end`` offsets index into — the merge/citation step slices
    it (instead of the stale persisted ``SourceDoc.text``) to rebuild verbatim
    passage text for the shadowed doc."""

    virtual_chunks: list[DocChunk]
    shadow_doc_id: str
    virtual_text: str = ""


@dataclass(frozen=True)
class LocationFilter:
    """Issue #263: a deterministic structural scope for retrieval — "this
    document, pages 30-90" / "this sheet". It NARROWS the candidate chunk set
    (pushed into both the dense vector query and the BM25/MMR corpus) BEFORE
    ranking, so a scoped query like "為什麼 XXX，據 30-90 頁" is vector ranking
    within the range, not a separate path. All fields optional; an all-None
    filter is a no-op (same as today's unscoped search).

    Page semantics: both bounds → inclusive range; exactly one bound → that
    single page (the tool sends `page_from` only for "page 30"). `sheet` is an
    exact match.
    """

    source_doc_id: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    sheet: str | None = None

    def is_empty(self) -> bool:
        return (
            self.source_doc_id is None
            and self.page_from is None
            and self.page_to is None
            and self.sheet is None
        )

    def conditions(self) -> list[ConditionBuilder]:
        """The QB predicates this filter contributes — AND-combined onto the
        collection scope in both retrieval queries. Each locator is an indexed
        field on DocChunk (see `resources` add_model), so this is a real indexed
        WHERE, never a Python post-filter."""
        conds: list[ConditionBuilder] = []
        if self.source_doc_id is not None:
            conds.append(QB["source_doc_id"] == self.source_doc_id)
        lo, hi = self.page_from, self.page_to
        if lo is not None and hi is not None:
            conds.append(QB["page"].between(lo, hi))
        elif lo is not None:
            conds.append(QB["page"] == lo)
        elif hi is not None:
            conds.append(QB["page"] == hi)
        if self.sheet is not None:
            conds.append(QB["sheet"] == self.sheet)
        return conds


@dataclass(frozen=True)
class _ResolvedEnhancements:
    expand: int
    hyde: int
    rerank: bool


def _resolve_enhancements(
    caller: Enhancements | None,
    defaults: EnhancementSettings,
) -> _ResolvedEnhancements:
    """Merge caller-supplied overrides on top of operator defaults,
    then clamp by the operator's max. Centralised so the search loop
    reads three plain ints/bools and doesn't repeat the cascade."""
    expand_raw = (
        caller.expand
        if caller is not None and caller.expand is not None
        else defaults.expand.default
    )
    hyde_raw = (
        caller.hyde if caller is not None and caller.hyde is not None else defaults.hyde.default
    )
    rerank_raw = (
        caller.rerank
        if caller is not None and caller.rerank is not None
        else defaults.rerank.default
    )
    return _ResolvedEnhancements(
        expand=min(max(0, expand_raw), defaults.expand.max),
        hyde=min(max(0, hyde_raw), defaults.hyde.max),
        rerank=bool(rerank_raw) and bool(defaults.rerank.max),
    )


def _scoped(
    base: ConditionBuilder,
    location: LocationFilter | None,
    exclude_doc_ids: frozenset[str] = frozenset(),
) -> ConditionBuilder:
    """AND the location filter's predicates onto a base query, then EXCLUDE any
    denied docs (#308 — the per-doc read override the speaker can't see). `None`/
    empty location + empty exclusion ⇒ the base unchanged (unscoped)."""
    out = base
    if location is not None:
        for cond in location.conditions():
            out = out & cond
    if exclude_doc_ids:
        out = out & QB["source_doc_id"].not_in(list(exclude_doc_ids))
    return out


def _saturate(x: float) -> float:
    """Smoothstep on [0, 1] — ``3x² − 2x³``. A parameter-free saturating curve
    (flat near 0 and 1) so #105's quality prior compresses the tails: a 0.95-vs-1.0
    quality gap barely moves the score while a 0.1-vs-0.5 gap does. The IR
    document-prior literature (Craswell et al. SIGIR'05) fits a sigmoid for exactly
    this reason; smoothstep is the dependency-free stand-in."""
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


class _DocJoin:
    """Batched SourceDoc joins for ONE search — the ranking tail's N+1 cure.

    For every candidate chunk the tail needs three things: the chunk's RESOLVED
    (content→canonical) doc id, that doc's display ``path``, and its
    ``quality_score``. Resolved one chunk at a time that is several store round
    trips PER CANDIDATE — free-looking on the in-memory backend, brutal over a
    Postgres connection, and the dominant cost once the bulk loads are gone. This
    resolves the whole candidate set up front in at most TWO queries and then
    answers every lookup from memory.

    Resolution is exactly the coalescing rule it replaces (#104): a chunk carrying a
    content ``source_file_id`` resolves to the earliest-created live doc sharing
    ``(collection_id, file_id)`` (``resource_id`` as the deterministic tiebreak); a
    legacy chunk — or content whose docs are all gone — falls back to the chunk's own
    ``source_doc_id``. A doc that no longer exists has no ``path``, and the caller
    drops that hit as a true orphan.

    Only small scalar fields are fetched, never ``text``: a doc's canonical text can
    be megabytes and is needed only for the handful of docs that actually render
    (:meth:`texts_for`, itself one batched query)."""

    # Everything the tail reads off a SourceDoc except `text` (see above).
    _META_FIELDS = ["/path", "/quality_score", "/collection_id", "/content/file_id"]

    def __init__(self, spec: SpecStar, chunks: Iterable[DocChunk]) -> None:
        self._spec = spec
        chunk_list = list(chunks)
        rm = spec.get_resource_manager(SourceDoc)
        self._canonical: dict[tuple[str, str], str] = {}
        self._path: dict[str, str] = {}
        self._quality: dict[str, int | None] = {}

        pairs = {(c.collection_id, c.source_file_id) for c in chunk_list if c.source_file_id}
        if pairs:
            # ONE query for every content key at once. `in_` × `in_` is a CROSS
            # PRODUCT, so it can also return (collection, file_id) combinations no
            # candidate asked for — harmless: resolution keys on the exact PAIR, so
            # an unrequested combination lands under its own key and is never looked
            # up (and identical content in two collections still resolves per
            # collection, never bleeding across).
            best: dict[tuple[str, str], tuple[float, str]] = {}
            query = (
                QB["collection_id"].in_(sorted({c for c, _ in pairs}))
                & QB["file_id"].in_(sorted({f for _, f in pairs}))
            ).build()
            for r in rm.list_resources(query, returns=["data", "info"], partial=self._META_FIELDS):
                rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
                doc = cast(SourceDoc, r.data)
                self._remember(rid, doc)
                key = (doc.collection_id, _content_file_id(doc))
                stamp = (r.info.created_time.timestamp(), rid)  # ty: ignore[unresolved-attribute]
                if key not in best or stamp < best[key]:
                    best[key] = stamp
                    self._canonical[key] = rid

        # The fallback owners (legacy chunks, and content whose docs are all gone) —
        # one more query so those resolve without a per-chunk point get either.
        fallback = {c.source_doc_id for c in chunk_list if c.source_doc_id} - set(self._path)
        if fallback:
            query = QB.resource_id().in_(sorted(fallback)).build()
            for r in rm.list_resources(query, returns=["data", "info"], partial=self._META_FIELDS):
                self._remember(
                    r.info.resource_id,  # ty: ignore[unresolved-attribute]
                    cast(SourceDoc, r.data),
                )

    def _remember(self, resource_id: str, doc: SourceDoc) -> None:
        self._path[resource_id] = doc.path
        self._quality[resource_id] = doc.quality_score

    def doc_id_for(self, chunk: DocChunk) -> str | None:
        """The chunk's live SourceDoc id by CONTENT, falling back to its own
        ``source_doc_id``; ``None`` when there is no id to try."""
        if chunk.source_file_id:
            canonical = self._canonical.get((chunk.collection_id, chunk.source_file_id))
            if canonical is not None:
                return canonical
        return chunk.source_doc_id or None

    def load(self, doc_ids: Iterable[str]) -> None:
        """Batch-load metadata for docs OUTSIDE the candidate set — the attachment
        parents (#513 P9), which are discovered only after ranking. One query for
        the ids not already known; a no-op when they all are."""
        missing = sorted(set(doc_ids) - set(self._path))
        if not missing:
            return
        rm = self._spec.get_resource_manager(SourceDoc)
        query = QB.resource_id().in_(missing).build()
        for r in rm.list_resources(query, returns=["data", "info"], partial=self._META_FIELDS):
            self._remember(
                r.info.resource_id,  # ty: ignore[unresolved-attribute]
                cast(SourceDoc, r.data),
            )

    def path_of(self, doc_id: str) -> str | None:
        """The doc's stored path (the display filename's source), or ``None`` when
        the doc is gone — never derived by parsing the opaque id."""
        return self._path.get(doc_id)

    def quality_of(self, doc_id: str) -> int | None:
        """The doc's ``quality_score``; ``None`` = un-scored = neutral (#105)."""
        return self._quality.get(doc_id)

    def texts_for(self, doc_ids: Iterable[str]) -> dict[str, str]:
        """Canonical text for the docs about to render, in ONE query. Chunk offsets
        index into the parser's extracted ``text``, so that is the canonical source.
        A legacy row with no stored ``text`` is omitted — the caller falls back to
        the per-doc byte decode, rare enough to stay a point read."""
        wanted = sorted(set(doc_ids))
        if not wanted:
            return {}
        rm = self._spec.get_resource_manager(SourceDoc)
        query = QB.resource_id().in_(wanted).build()
        out: dict[str, str] = {}
        for r in rm.list_resources(query, returns=["data", "info"], partial=["/text"]):
            text = cast(SourceDoc, r.data).text
            if text is not None:
                out[r.info.resource_id] = text  # ty: ignore[unresolved-attribute]
        return out


def _content_file_id(doc: SourceDoc) -> str:
    """A doc's content hash, or ``""`` when unset — the content key chunks join on."""
    fid = getattr(doc.content, "file_id", None)
    return fid if isinstance(fid, str) else ""


def _chunk_vec(chunk: DocChunk) -> list[float]:
    """Read whichever vector field the chunk's collection populated. P3.0
    chunks use exactly one of `embedding` (default text model) or
    `embedding_alt` (code model); empty list returned for the (impossible)
    null/null case so MMR returns cosine=1 (max distance) rather than crash."""
    return chunk.embedding or chunk.embedding_alt or []


class Retriever:
    def __init__(
        self,
        spec: SpecStar,
        *,
        embedder: Embedder,
        llm: ILlm | None = None,
        candidates: int = 20,
        top_k: int = 5,
        code_embedder: Embedder | None = None,
        image_embedder: ImageEmbedder | None = None,
        enhancement_defaults: EnhancementSettings | None = None,
        quality_weight: float = 0.10,
        quality_floor: int | None = None,
        disclosure_floor: float = 0.6,
    ) -> None:
        self._spec = spec
        self._embedder = embedder
        self._llm = llm
        self._candidates = candidates
        self._top_k = top_k
        # P3.0 §2.9 D1+parallel: a code-specialised embedder for the
        # `embedding_alt` field. When wired, the dense pass fans out over
        # both vector fields in parallel (one rank per field, RRF-merged).
        # None ⇒ retriever stays single-field (legacy `embedding` only).
        self._code_embedder = code_embedder
        # #513: an image embedder for the `embedding_img` field. When wired AND
        # the model supports text queries (CLIP-style shared space), the dense pass
        # gains a text→image arm. None, or an image-only model, adds nothing — the
        # text path is untouched (image-to-image search is a separate entry point).
        self._image_embedder = image_embedder
        # Operator-level enhancement defaults + ceilings. None → bundled
        # `EnhancementSettings()` (light: expand=1, hyde=0, rerank=on).
        # Each `search` call's caller / LLM tool args resolve against
        # these via `_resolve_enhancements`.
        self._enhancement_defaults = enhancement_defaults or EnhancementSettings()
        # #105: the second-phase document-quality prior. `quality_weight` (w) is the
        # prior's strength — kept SMALL relative to the normalized relevance so a
        # real relevance gap always wins (the literature's warning: an over-strong
        # prior nukes recall). `quality_floor` is an OPTIONAL absolute hard cutoff
        # (docs scored below it are dropped); None (the default) = soft only, never
        # exclude. Both are operator config (config.example.yaml).
        self._quality_weight = quality_weight
        self._quality_floor = quality_floor
        # Permission-disclosure probe (D9): the absolute cosine-distance ceiling a
        # withheld collection's best chunk must clear to be disclosed when nothing
        # is readable (or when the readable results are near-worthless — it caps a
        # loose "beat the weakest shown" cutoff, so pure noise is never disclosed).
        # A hyperparameter; tune per embedder. cosine distance ∈ [0, 2]; 0.6 ≈
        # cosine similarity ≥ 0.4.
        self._disclosure_floor = disclosure_floor

    @property
    def top_k(self) -> int:
        """The user-facing result cut — how many passages a normal search
        returns. The findability probe reads it to mark which deep ranks fall
        within "what the user actually sees"."""
        return self._top_k

    def search(
        self,
        query: str,
        collection_ids: list[str],
        on_progress: OnChunk | None = None,
        *,
        enhancements: Enhancements | None = None,
        location: LocationFilter | None = None,
        overlay: Overlay | None = None,
        depth: int | None = None,
        exclude_doc_ids: frozenset[str] = frozenset(),
    ) -> list[RetrievedPassage]:
        """`enhancements` is the per-call override: any field set to a
        concrete value wins over the operator default for that knob,
        then is clamped against the operator's `max`. Fields left
        `None` (or the whole arg `None`) inherit from the operator
        default. When the LLM isn't wired, all three are forced off
        regardless of the resolved values.

        `location` (#263) is a deterministic structural scope (document /
        page range / sheet) pushed into BOTH the dense vector query and the
        BM25/MMR corpus, so ranking happens within the scope. `None` (or an
        empty filter) ⇒ unscoped, exactly as before.

        `overlay` (#328) is a dry-run candidate-set override: the shadowed doc's
        stored chunks are dropped and the supplied virtual chunks added, so a
        findability preview ranks as if the doc were re-parsed — no reindex. When
        set, the dense order is recomputed in-memory (same cosine metric);
        everything else runs unchanged over the overlaid chunk set.

        `depth` (#328) widens the result: when set, the candidate pool and the
        MMR window grow to `depth` and the full ranked passage list (up to
        `depth`) is returned instead of the `top_k` slice — the findability probe
        reads where a doc's chunk ranks beyond the 5 a user normally sees. `None`
        (the default) is byte-for-byte the previous behaviour."""
        cand = depth if depth is not None else self._candidates
        mmr_k = depth if depth is not None else self._top_k * 3
        limit = depth if depth is not None else self._top_k
        loc = location if location is not None and not location.is_empty() else None
        logger.info(
            "retriever: search start query=%r collections=%s candidates=%d",
            query,
            collection_ids,
            cand,
        )
        # The overlay path (#328) rebuilds the whole candidate set in memory (drop the
        # shadowed doc's chunks, add the virtual ones) and recomputes the dense order
        # over it, so it alone loads every chunk WITH its vector. The NORMAL path never
        # loads the whole collection: its sparse corpus is trigram-narrowed to the
        # lexical matches (`_sparse_corpus`) and the fused candidates' chunks are
        # hydrated by id below. #308 excludes chunks of docs the speaker's per-doc
        # override blocks from BOTH the sparse corpus and the dense query, so a hidden
        # doc never reaches ranking/answer.
        overlay_chunks: dict[str, DocChunk] = {}
        if overlay is not None:
            overlay_chunks = self._load_chunks(collection_ids, loc, exclude_doc_ids)
            # #104: the shadowed doc's REAL chunks are its CONTENT chunks — match
            # by the shadow doc's file_id (an aliased doc's content is owned by a
            # canonical sibling, so a source_doc_id match alone would miss them),
            # with a source_doc_id fallback for legacy (source_file_id == "") chunks.
            shadow_fid = self._doc_file_id(overlay.shadow_doc_id)
            overlay_chunks = {
                cid: ch
                for cid, ch in overlay_chunks.items()
                if not (
                    (shadow_fid and ch.source_file_id == shadow_fid)
                    or ch.source_doc_id == overlay.shadow_doc_id
                )
            }
            for i, vc in enumerate(overlay.virtual_chunks):
                overlay_chunks[f"__overlay__{i}"] = vc
            logger.debug("retriever: overlay chunk universe size=%d", len(overlay_chunks))
            if not overlay_chunks:
                return []

        def step(label: str) -> None:
            if on_progress is not None:
                on_progress(label, False)  # a step header (not model reasoning)

        resolved = _resolve_enhancements(enhancements, self._enhancement_defaults)
        # An LLM-less retriever can't run any enhancement — force them
        # off so the loop below behaves the same as when operator
        # clamped everything to zero.
        if self._llm is None:
            resolved = _ResolvedEnhancements(expand=0, hyde=0, rerank=False)

        # Multi-query: an LLM (when wired and `expand>0`) widens recall
        # with alternative phrasings; each variant contributes a dense
        # + a sparse ranked list to the fusion.
        if resolved.expand > 0:
            assert self._llm is not None  # resolved.expand>0 implies llm wired
            step("\n↻ expanding query\n")
            logger.debug("retriever: multi-query expand into %d variants", resolved.expand)
            queries = expand_queries(self._llm, query, n=resolved.expand, on_progress=on_progress)
        else:
            queries = [query]
        # The sparse (BM25) corpus. Overlay ranks over its explicit in-memory set;
        # the normal path narrows to the chunks trigram-similar to a query term
        # (`_sparse_corpus`) so it never loads + tokenizes the whole collection. BM25
        # only scores chunks containing a query term, and a fuzzy match is a superset
        # of an exact-term match, so every chunk BM25 would rank is present (2a).
        if overlay is not None:
            corpus = [(cid, ch.text) for cid, ch in overlay_chunks.items()]
        else:
            corpus = self._sparse_corpus(queries, collection_ids, loc, exclude_doc_ids)
        ranked_lists: list[list[str]] = []
        for q in queries:
            qv = self._embedder.embed_query(q)
            ranked_lists.append(
                self._dense(
                    vec=qv,
                    field="embedding",
                    chunks=overlay_chunks,
                    cids=collection_ids,
                    loc=loc,
                    overlay=overlay,
                    exclude_doc_ids=exclude_doc_ids,
                )
            )
            # P3.0 fan-out: a separate dense pass for the code-vector field
            # (when wired). Each field uses its own embedder, so the query
            # vector is in the right geometry.
            if self._code_embedder is not None:
                qv_alt = self._code_embedder.embed_query(q)
                ranked_lists.append(
                    self._dense(
                        vec=qv_alt,
                        field="embedding_alt",
                        chunks=overlay_chunks,
                        cids=collection_ids,
                        loc=loc,
                        overlay=overlay,
                    )
                )
            # #513 text→image arm: only for a shared-space image model. Image-only
            # (embed_query_text → None) or no image embedder ⇒ nothing appended.
            if self._image_embedder is not None:
                qv_img = self._image_embedder.embed_query_text(q)
                if qv_img is not None:
                    ranked_lists.append(
                        self._dense(
                            vec=qv_img,
                            field="embedding_img",
                            chunks=overlay_chunks,
                            cids=collection_ids,
                            loc=loc,
                            overlay=overlay,
                        )
                    )
            ranked_lists.append(bm25_rank(q, corpus))

        # HyDE: embed N hypothetical answers (as pseudo-documents); each
        # adds another dense probe so retrieval matches answer-shaped
        # text. `resolved.hyde` is the count (0 = skip).
        if resolved.hyde > 0:
            assert self._llm is not None
            llm = self._llm
            step("\n↻ HyDE\n")
            logger.debug("retriever: HyDE generating %d hypothetical docs", resolved.hyde)
            hyde_docs = [
                doc
                for doc in (
                    hypothetical_document(llm, query, on_progress=on_progress)
                    for _ in range(resolved.hyde)
                )
                if doc
            ]
            for doc in hyde_docs:
                hv = self._embedder.embed_documents([doc])[0]
                ranked_lists.append(
                    self._dense(
                        vec=hv,
                        field="embedding",
                        chunks=overlay_chunks,
                        cids=collection_ids,
                        loc=loc,
                        overlay=overlay,
                    )
                )
                if self._code_embedder is not None:
                    hv_alt = self._code_embedder.embed_documents([doc])[0]
                    ranked_lists.append(
                        self._dense(
                            vec=hv_alt,
                            field="embedding_alt",
                            chunks=overlay_chunks,
                            cids=collection_ids,
                            loc=loc,
                            overlay=overlay,
                        )
                    )

        fused_score = rrf_scores(ranked_lists)
        fused = sorted(fused_score, key=lambda key: (-fused_score[key], key))[:cand]
        logger.debug(
            "retriever: RRF fused %d candidates from %d ranked lists",
            len(fused),
            len(ranked_lists),
        )

        if not fused:
            return []

        # Hydrate the fused candidates' chunks (metadata + vector) — the ONLY chunks
        # the ranking tail (MMR, quality prior, passage build) reads. Overlay already
        # holds them in memory; the normal path point-reads them by id, so nothing
        # downstream needs the whole-collection load either. A normal-path id whose
        # chunk vanished mid-query drops out of `fused` (a deleted chunk is no result).
        if overlay is not None:
            cand_chunks = {cid: overlay_chunks[cid] for cid in fused}
        else:
            cand_chunks = self._hydrate_chunks(fused)
            fused = [f for f in fused if f in cand_chunks]
        vec_of = {cid: _chunk_vec(ch) for cid, ch in cand_chunks.items()}

        # RRF order → descending relevance scores; MMR for diversity (cosine of
        # the stored chunk vectors as the similarity).
        relevance = {cid: 1.0 / (rank + 1) for rank, cid in enumerate(fused)}
        order = mmr(
            fused,
            relevance=relevance,
            # Either `embedding` (default text vector) or `embedding_alt`
            # (code vector) is populated per chunk. P3.0 retriever fan-out
            # keeps each path's vectors homogeneous (same field), so the MMR
            # similarity safely reads whichever is set.
            similarity=lambda a, b: 1.0 - cosine_distance(vec_of.get(a, []), vec_of.get(b, [])),
            k=mmr_k,
        )

        # Resolve every candidate's doc (id → path / quality) in two batched queries
        # instead of several point reads per candidate — see `_DocJoin`.
        join = _DocJoin(self._spec, cand_chunks.values())

        # #105: second-phase document-quality prior. Recall (RRF + MMR above) is
        # unchanged; this only re-scores the surviving candidates.
        order, score_of = self._apply_quality_prior(
            order, cand_chunks, relevance, fused_score, join
        )

        # The display filename is the basename of the SourceDoc's stored path —
        # the id is opaque and must not be parsed for it.
        scored = []
        for cid in order:
            doc_id = join.doc_id_for(cand_chunks[cid])
            path = None if doc_id is None else join.path_of(doc_id)
            if doc_id is None or path is None:
                continue  # #104: true orphan (neither file_id nor owner resolves) — drop
            scored.append(
                ScoredChunk(
                    chunk_id=cid,
                    document_id=doc_id,
                    collection_id=cand_chunks[cid].collection_id,
                    filename=posixpath.basename(path),
                    seq=cand_chunks[cid].seq,
                    start=cand_chunks[cid].start,
                    end=cand_chunks[cid].end,
                    score=score_of[cid],
                    provenance=cand_chunks[cid].provenance,
                )
            )
        # One batched read for the texts the passages slice, rather than a get per
        # rendered doc. A legacy row with no stored `text` falls through to the
        # per-doc byte decode (`_canonical_text`), and #328's shadowed doc reads the
        # re-parsed `virtual_text` its offsets index into.
        texts = join.texts_for(c.document_id for c in scored)

        def text_of(doc_id: str) -> str:
            if overlay is not None and doc_id == overlay.shadow_doc_id:
                return overlay.virtual_text
            cached = texts.get(doc_id)
            return cached if cached is not None else self._canonical_text(doc_id)

        passages = merge_passages(scored, text_of=text_of)
        # Final LLM rerank over the merged passages — bool knob.
        if resolved.rerank:
            assert self._llm is not None
            step("\n↻ rerank\n")
            logger.debug("retriever: reranking %d merged passages via llm", len(passages))
            passages = rerank_passages(self._llm, query, passages, on_progress=on_progress)
        logger.info("retriever: search complete, ranked=%d limit=%d", len(passages), limit)
        # #513 P9: pull in the parent of any attachment hit — AFTER the top_k cut,
        # so the parent context rides along without displacing a primary result.
        return self._augment_with_parents(passages[:limit], join)

    def _augment_with_parents(
        self, passages: list[RetrievedPassage], join: _DocJoin
    ) -> list[RetrievedPassage]:
        """#513 P9 — attachment-aware parent merge. A hit on an attachment's chunk
        (often a thin image figure) is semantically thin on its own: the
        surrounding explanation lives in the PARENT document's text. So each
        attachment passage additionally pulls in its parent document (its full text, one
        passage, inheriting the attachment's score so it sits alongside). Deduped:
        a parent already present — independently hit, or shared by two attachment
        hits — is never appended twice. Non-attachment passages are untouched, so
        an attachment-free collection returns byte-for-byte the same list.

        One batched read resolves every passage's `parent_doc_id` (an
        attachment-free collection stops right there), so this stays a fixed couple
        of round trips rather than one per rendered passage."""
        present = {p.document_id for p in passages}
        parents = self._parent_doc_ids([p.document_id for p in passages])
        wanted = [
            (p, parents[p.document_id])
            for p in passages
            if parents.get(p.document_id) and parents[p.document_id] not in present
        ]
        if not wanted:
            return passages
        # Reuses the search's join: a parent that was itself a candidate is already
        # loaded, so this costs a query only for parents outside the candidate set.
        join.load(pid for _, pid in wanted)
        texts = join.texts_for(pid for _, pid in wanted)
        extra: list[RetrievedPassage] = []
        for p, parent_id in wanted:
            if parent_id in present:
                continue  # a second attachment hit sharing the same parent
            text = texts.get(parent_id)
            path = join.path_of(parent_id)
            if text is None or path is None:  # pragma: no cover — a live parent has both
                continue
            extra.append(
                RetrievedPassage(
                    collection_id=p.collection_id,
                    document_id=parent_id,
                    filename=posixpath.basename(path),
                    start=0,
                    end=len(text),
                    source_chunk_ids=[],
                    text=text,
                    score=p.score,
                )
            )
            present.add(parent_id)
        return passages + extra

    def _parent_doc_ids(self, doc_ids: list[str]) -> dict[str, str]:
        """``doc_id -> parent_doc_id`` for the given docs in ONE query — non-empty
        iff the doc is an attachment (#513 P7). A doc that has since been deleted
        simply doesn't come back, and reads as no parent."""
        if not doc_ids:
            return {}
        rm = self._spec.get_resource_manager(SourceDoc)
        query = QB.resource_id().in_(sorted(set(doc_ids))).build()
        out: dict[str, str] = {}
        for r in rm.list_resources(query, returns=["data", "info"], partial=["/parent_doc_id"]):
            out[r.info.resource_id] = cast(SourceDoc, r.data).parent_doc_id  # ty: ignore[unresolved-attribute]
        return out

    def _apply_quality_prior(
        self,
        order: list[str],
        chunks: dict[str, DocChunk],
        relevance: dict[str, float],
        fused_score: dict[str, float],
        join: _DocJoin,
    ) -> tuple[list[str], dict[str, float]]:
        """#105 — fold each candidate's document-quality score into its final score
        as a **second-phase additive document prior**, the form the IR
        document-prior literature supports (Craswell, Robertson, Zaragoza & Taylor,
        *Relevance Weighting for Query-Independent Evidence*, SIGIR 2005; Kraaij,
        Westerveld & Hiemstra, *The Importance of Prior Probabilities for Entry Page
        Search*, SIGIR 2002; Zhou & Croft, *Document Quality Models for Web Ad Hoc
        Retrieval*, CIKM 2005; Vespa/Azure phased ranking):

            final = R + w · (saturate(quality/100) − 0.5)

        - ``R`` is the **normalized RRF fused score** — a magnitude-bearing relevance
          (it keeps both the dense and the BM25 evidence, unlike a raw cosine which
          would discard the sparse signal, and unlike the bare ``1/(rank+1)`` which
          discards magnitude). Quality combines with *how relevant* a doc is, not
          just its rank position.
        - The prior is **additive** (theory's preferred form — a naive multiply lets
          a skewed feature swamp relevance), **centered** at 0.5 so an *un-scored*
          doc contributes exactly ``+0`` (the neutral midpoint, never "worst"),
          **saturating** so a 95-vs-100 gap barely matters while a 10-vs-50 gap does,
          and weighted by a **small** ``w`` so a real relevance gap always wins.
        - It is **soft**: a low-quality doc is demoted, never dropped — unless an
          operator sets an absolute ``quality_floor`` (off by default).
        - It is **scoped**: when no candidate doc carries a score, ranking is left
          byte-for-byte as before (the existing rank-based ``relevance``), so
          collections without a rubric are unaffected.
        """

        def q_of(cid: str) -> int | None:
            # #104: score by the chunk's RESOLVED (content→canonical) doc, so
            # quality follows content exactly like citation resolution does. Both
            # lookups are served from the batched `_DocJoin`, never a per-candidate
            # store read.
            doc_id = join.doc_id_for(chunks[cid])
            return None if doc_id is None else join.quality_of(doc_id)

        if not any(q_of(cid) is not None for cid in order):
            return order, {cid: relevance[cid] for cid in order}
        if self._quality_floor is not None:
            floor = self._quality_floor
            order = [cid for cid in order if (q := q_of(cid)) is None or q >= floor]
        norm = max((fused_score[cid] for cid in order), default=1.0) or 1.0
        score_of: dict[str, float] = {}
        for cid in order:
            q = q_of(cid)
            prior = 0.0 if q is None else self._quality_weight * (_saturate(q / 100.0) - 0.5)
            score_of[cid] = fused_score[cid] / norm + prior
        return order, score_of

    def _dense(
        self,
        *,
        vec: list[float],
        field: str,
        chunks: dict[str, DocChunk],
        cids: list[str],
        loc: LocationFilter | None,
        overlay: Overlay | None,
        exclude_doc_ids: frozenset[str] = frozenset(),
    ) -> list[str]:
        """Dense ranking for one query vector. Normal path pushes the cosine sort
        into the store (pgvector). The #328 overlay path recomputes it in-memory
        over the overlaid chunk set — the virtual chunks aren't in the store — with
        the SAME cosine metric, so only the candidate set differs, not the order's
        geometry."""
        if overlay is not None:
            return self._dense_order_mem(chunks, vec, field=field)
        return self._dense_order(
            cids, vec, field=field, location=loc, exclude_doc_ids=exclude_doc_ids
        )

    def probe_withheld(
        self,
        query: str,
        readable_collection_ids: list[str],
        withheld_collection_ids: list[str],
    ) -> list[str]:
        """The subset of ``withheld_collection_ids`` that hold a COMPETITIVE match
        for ``query`` — used to disclose "there IS an answer here you can't read"
        without leaking the answer (permission-disclosure, D9).

        A withheld collection is disclosed iff its best (nearest) chunk's cosine
        distance ≤ ``min(the weakest readable top-k distance, disclosure_floor)`` —
        i.e. at least as relevant as something we DID show (competitive) AND
        absolutely relevant (the floor is the noise guard, and the sole test when
        nothing is readable). SCORES-ONLY: this returns only collection ids; the
        withheld chunks' text and vectors never leave this method, so nothing
        enters the agent context or the readable passage list. The main ``search``
        path is entirely untouched — this is a separate dense pass, by design."""
        withheld = [c for c in dict.fromkeys(withheld_collection_ids) if c]
        if not withheld:
            return []
        qv = self._embedder.embed_query(query)
        # The cutoff: the weakest readable top-k distance, capped by the floor.
        # An empty/contentless readable scope collapses to the floor alone.
        readable_scored = self._dense_scored(list(readable_collection_ids), qv, limit=self._top_k)
        cutoff = min(
            max((dist for _, dist in readable_scored), default=self._disclosure_floor),
            self._disclosure_floor,
        )
        # Best (nearest) distance per withheld collection, one dense pass.
        best: dict[str, float] = {}
        for cid, dist in self._dense_scored(withheld, qv, limit=self._candidates):
            if cid not in best or dist < best[cid]:
                best[cid] = dist
        disclosed = [c for c in withheld if c in best and best[c] <= cutoff]
        logger.info(
            "retriever: disclosure probe cutoff=%.4f readable=%d withheld=%d disclosed=%d",
            cutoff,
            len(readable_collection_ids),
            len(withheld),
            len(disclosed),
        )
        return disclosed

    def _dense_scored(
        self,
        collection_ids: list[str],
        vec: list[float],
        *,
        field: str = "embedding",
        limit: int,
    ) -> list[tuple[str, float]]:
        """``(collection_id, cosine_distance)`` for the ``limit`` nearest chunks to
        ``vec`` across ``collection_ids``, nearest-first, via specstar's native
        vector query. Backs the disclosure probe: the store returns the ORDER, and
        we read each hit's stored vector to recover the DISTANCE magnitude needed to
        compare two scopes. Reads ONLY ``collection_id`` + the vector off each hit —
        never the chunk text — so nothing readable leaks through it."""
        if not collection_ids:
            return []
        rm = self._spec.get_resource_manager(DocChunk)
        query = (
            QB["collection_id"]
            .in_(collection_ids)
            # specstar's order_by type union omits VectorDistanceSort (works at runtime)
            .order_by(QB[field].cosine(vec).asc())  # ty: ignore[invalid-argument-type]
            .limit(limit)
            .build()
        )
        out: list[tuple[str, float]] = []
        for r in rm.list_resources(query):
            ch = r.data
            assert isinstance(ch, DocChunk)
            cv = _chunk_vec(ch)
            if cv:
                out.append((ch.collection_id, cosine_distance(cv, vec)))
        return out

    def _dense_order_mem(
        self, chunks: dict[str, DocChunk], vec: list[float], *, field: str
    ) -> list[str]:
        """In-memory dense order for the #328 overlay — nearest-first by cosine
        over the explicit (overlaid) chunk set, capped at ``candidates``. Cosine
        is the store's metric too, so this matches the persisted-vector order."""
        scored: list[tuple[float, str]] = []
        for cid, ch in chunks.items():
            # field is one of embedding / embedding_alt / embedding_img (#513).
            v: list[float] | None = getattr(ch, field)
            if v:
                scored.append((cosine_distance(v, vec), cid))
        scored.sort()
        return [cid for _, cid in scored[: self._candidates]]

    def _dense_order(
        self,
        collection_ids: list[str],
        vec: list[float],
        *,
        field: str = "embedding",
        location: LocationFilter | None = None,
        exclude_doc_ids: frozenset[str] = frozenset(),
    ) -> list[str]:
        """Top candidate chunk ids nearest `vec` in the given vector `field`
        (``embedding`` or ``embedding_alt``), via specstar's native vector
        query (pgvector-indexed when available; computed in-store otherwise).
        Ordered nearest-first by cosine distance. A `location` filter (#263)
        AND-narrows the candidate set on the indexed provenance fields BEFORE
        the vector sort — index filter + vector order in ONE query, the same
        way `collection_id` already scopes it."""
        rm = self._spec.get_resource_manager(DocChunk)
        scope = _scoped(QB["collection_id"].in_(collection_ids), location, exclude_doc_ids)
        query = (
            scope
            # specstar's order_by type union omits VectorDistanceSort (works at runtime)
            .order_by(QB[field].cosine(vec).asc())  # ty: ignore[invalid-argument-type]
            .limit(self._candidates)
            .build()
        )
        # We only read the ranked ids — `returns=["info"]` keeps `r.info` (the
        # resource id) and drops `r.data`, so the vector sort runs in the store
        # (pgvector) without shipping each candidate's 4096-d embedding back.
        return [
            r.info.resource_id  # ty: ignore[unresolved-attribute]
            for r in rm.list_resources(query, returns=["info"])
        ]

    def _load_chunks(
        self,
        collection_ids: list[str],
        location: LocationFilter | None = None,
        exclude_doc_ids: frozenset[str] = frozenset(),
    ) -> dict[str, DocChunk]:
        """The FULL in-memory chunk set (with vectors) for the #328 overlay path,
        which rebuilds the candidate set (drop the shadowed doc's chunks, add the
        virtual ones) and recomputes the dense order over it. A `location` filter
        scopes it the SAME way as the dense query. The NORMAL path never calls this —
        it narrows the sparse corpus (`_sparse_corpus`) and hydrates only the fused
        candidates (`_hydrate_chunks`), so it never loads the whole collection."""
        rm = self._spec.get_resource_manager(DocChunk)
        out: dict[str, DocChunk] = {}
        for cid in collection_ids:
            scope = _scoped(QB["collection_id"] == cid, location, exclude_doc_ids)
            for r in rm.list_resources(scope.build()):
                data = r.data
                assert isinstance(data, DocChunk)
                out[r.info.resource_id] = data  # ty: ignore[unresolved-attribute]
        return out

    def _hydrate_chunks(self, chunk_ids: list[str]) -> dict[str, DocChunk]:
        """The FULL chunk (metadata + vector) for each of the given fused-candidate
        ids — the ONLY chunks the ranking tail reads (MMR similarity, quality prior,
        passage/citation metadata). ONE batched read by id — not a point get per
        candidate — so the normal path never loads the whole collection and its round
        trips stay flat as the candidate pool grows. An id whose chunk vanished
        mid-query simply doesn't come back (a deleted chunk is dropped from the
        results)."""
        rm = self._spec.get_resource_manager(DocChunk)
        out: dict[str, DocChunk] = {}
        for r in rm.list_resources(QB.resource_id().in_(sorted(set(chunk_ids))).build()):
            data = r.data
            assert isinstance(data, DocChunk)  # full records — MMR needs the vectors
            out[r.info.resource_id] = data  # ty: ignore[unresolved-attribute]
        return out

    def _sparse_corpus(
        self,
        queries: list[str],
        collection_ids: list[str],
        location: LocationFilter | None,
        exclude_doc_ids: frozenset[str],
    ) -> list[tuple[str, str]]:
        """The BM25 corpus, narrowed to the chunks trigram-similar to a query term
        (2a). Instead of loading + tokenizing every chunk's text, `.fuzzy(term)` (a
        pg_trgm GIN filter, `text`'s `TrigramIndex`) pre-selects the lexical
        candidates. BM25 only ever scores chunks containing a query term, and a fuzzy
        match is a superset of an exact-term match, so every chunk BM25 would rank is
        present — the ranking is preserved, only the loaded set shrinks. `location` /
        `exclude_doc_ids` scope it exactly as the dense query. Text projected only
        (`partial`), so no vector is deserialized here either.

        No query term (a punctuation-only query) ⇒ empty corpus, exactly as BM25's
        own `q_terms`-empty guard would produce."""
        terms = {t for q in queries for t in tokenize(q)}
        if not terms:
            return []
        rm = self._spec.get_resource_manager(DocChunk)
        out: dict[str, str] = {}
        for cid in collection_ids:
            fuzzy: ConditionBuilder | None = None
            for t in terms:
                cond = QB["text"].fuzzy(t)
                fuzzy = cond if fuzzy is None else (fuzzy | cond)
            assert fuzzy is not None  # `terms` is non-empty
            scope = _scoped((QB["collection_id"] == cid) & fuzzy, location, exclude_doc_ids)
            for r in rm.list_resources(scope.build(), returns=["data", "info"], partial=["/text"]):
                out[r.info.resource_id] = cast(DocChunk, r.data).text  # ty: ignore[unresolved-attribute]
        return list(out.items())

    def _doc_file_id(self, doc_id: str) -> str:
        """A doc's content hash (``content.file_id``), or ``""`` if the doc is
        gone / unset — used to match its content-addressed chunks (#104)."""
        rm = self._spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError:
            logger.debug("retriever: doc %s gone, no content file_id", doc_id)
            return ""
        assert isinstance(doc, SourceDoc)
        fid = doc.content.file_id
        return fid if isinstance(fid, str) else ""

    def _canonical_text(self, doc_id: str) -> str:
        """The fallback for a doc the batched `texts_for` read could not serve: a
        LEGACY row predating stored `text`, or one deleted mid-query. Chunk offsets
        index into the parser's extracted text, so a row that HAS `text` is always
        served by the batch and never reaches here."""
        rm = self._spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError:  # pragma: no cover — chunk implies its doc exists
            return ""
        assert isinstance(doc, SourceDoc)
        if doc.text is not None:  # pragma: no cover — the batch already served these
            return doc.text
        # Legacy rows predating stored `text`: decode the raw bytes only when
        # they are clean UTF-8 (a plain-text upload). A binary blob with no
        # extracted text gets a readable marker, not replacement-char garbage —
        # reindex such a doc to recover real content.
        raw = rm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)
        try:
            return normalize_text(raw.decode("utf-8"))
        except UnicodeDecodeError:
            logger.debug("retriever: doc %s has no extractable text (binary blob)", doc_id)
            return _NO_EXTRACTABLE_TEXT
