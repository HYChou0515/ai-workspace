"""Retriever — hybrid retrieval over a set of collections.

Pipeline: dense (specstar's native vector query over the stored chunk vectors —
pgvector-indexed when available) + sparse (BM25) → Reciprocal Rank Fusion → MMR
diversification → parent-document merge → top-k RetrievedPassage. The Embedder
is injected (asymmetric query embedding); the LLM-driven enhancements
(multi-query, HyDE, rerank) layer on top via an optional `llm`.

The chunk set is still loaded once for the sparse (BM25) corpus, MMR similarity,
and passage metadata; only the dense ranking is pushed into the store. Cosine is
the shared metric, so the dense order matches the stored vectors' geometry.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from specstar import QB, SpecStar
from specstar.query import ConditionBuilder
from specstar.types import ResourceIDNotFoundError
from specstar.util.vector_distance import cosine_distance

from ..config.schema import EnhancementSettings
from ..resources.kb import DocChunk, RetrievedPassage, SourceDoc
from .bm25 import bm25_rank
from .embedder import Embedder
from .fusion import mmr, rrf_scores
from .ingest import normalize_text
from .llm import ILlm, OnChunk
from .merge import ScoredChunk, merge_passages
from .query import expand_queries, hypothetical_document
from .rerank import rerank_passages

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
        enhancement_defaults: EnhancementSettings | None = None,
        quality_weight: float = 0.10,
        quality_floor: int | None = None,
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
        # {chunk_id: DocChunk}; #308 excludes chunks of docs the speaker's per-doc
        # override blocks, from BOTH the BM25/MMR corpus (this dict) and the dense
        # native-vector query below, so a hidden doc never reaches ranking/answer.
        chunks = self._load_chunks(collection_ids, loc, exclude_doc_ids)
        if overlay is not None:
            # #104: the shadowed doc's REAL chunks are its CONTENT chunks — match
            # by the shadow doc's file_id (an aliased doc's content is owned by a
            # canonical sibling, so a source_doc_id match alone would miss them),
            # with a source_doc_id fallback for legacy (source_file_id == "") chunks.
            shadow_fid = self._doc_file_id(overlay.shadow_doc_id)
            chunks = {
                cid: ch
                for cid, ch in chunks.items()
                if not (
                    (shadow_fid and ch.source_file_id == shadow_fid)
                    or ch.source_doc_id == overlay.shadow_doc_id
                )
            }
            for i, vc in enumerate(overlay.virtual_chunks):
                chunks[f"__overlay__{i}"] = vc
        if not chunks:
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
            queries = expand_queries(self._llm, query, n=resolved.expand, on_progress=on_progress)
        else:
            queries = [query]
        corpus = [(cid, ch.text) for cid, ch in chunks.items()]
        ranked_lists: list[list[str]] = []
        for q in queries:
            qv = self._embedder.embed_query(q)
            ranked_lists.append(
                self._dense(
                    vec=qv,
                    field="embedding",
                    chunks=chunks,
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
                        chunks=chunks,
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
                        chunks=chunks,
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
                            chunks=chunks,
                            cids=collection_ids,
                            loc=loc,
                            overlay=overlay,
                        )
                    )

        fused_score = rrf_scores(ranked_lists)
        fused = sorted(fused_score, key=lambda key: (-fused_score[key], key))[:cand]

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
            similarity=lambda a, b: (
                1.0 - cosine_distance(_chunk_vec(chunks[a]), _chunk_vec(chunks[b]))
            ),
            k=mmr_k,
        )

        # #105: second-phase document-quality prior. Recall (RRF + MMR above) is
        # unchanged; this only re-scores the surviving candidates.
        order, score_of = self._apply_quality_prior(order, chunks, relevance, fused_score)

        # The display filename is the basename of the SourceDoc's stored path —
        # the id is opaque and must not be parsed for it.
        scored = []
        for cid in order:
            doc_id = self._resolve_doc_id(chunks[cid])
            path = None if doc_id is None else self._doc_path(doc_id)
            if doc_id is None or path is None:
                continue  # #104: true orphan (neither file_id nor owner resolves) — drop
            scored.append(
                ScoredChunk(
                    chunk_id=cid,
                    document_id=doc_id,
                    collection_id=chunks[cid].collection_id,
                    filename=posixpath.basename(path),
                    seq=chunks[cid].seq,
                    start=chunks[cid].start,
                    end=chunks[cid].end,
                    score=score_of[cid],
                    provenance=chunks[cid].provenance,
                )
            )
        # #328: for the overlay's shadowed doc, slice the re-parsed `virtual_text`
        # (the offsets index into it) instead of the stale persisted SourceDoc.text.
        if overlay is not None:

            def text_of(doc_id: str) -> str:
                if doc_id == overlay.shadow_doc_id:
                    return overlay.virtual_text
                return self._canonical_text(doc_id)
        else:
            text_of = self._canonical_text
        passages = merge_passages(scored, text_of=text_of)
        # Final LLM rerank over the merged passages — bool knob.
        if resolved.rerank:
            assert self._llm is not None
            step("\n↻ rerank\n")
            passages = rerank_passages(self._llm, query, passages, on_progress=on_progress)
        return passages[:limit]

    def _apply_quality_prior(
        self,
        order: list[str],
        chunks: dict[str, DocChunk],
        relevance: dict[str, float],
        fused_score: dict[str, float],
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
        cache: dict[str, int | None] = {}

        def q_of(cid: str) -> int | None:
            # #104: score by the chunk's RESOLVED (content→canonical) doc, so
            # quality follows content exactly like citation resolution does.
            doc_id = self._resolve_doc_id(chunks[cid])
            if doc_id is None:
                return None
            if doc_id not in cache:
                cache[doc_id] = self._doc_quality(doc_id)
            return cache[doc_id]

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

    def _doc_quality(self, doc_id: str) -> int | None:
        """The doc's stored ``quality_score`` (``None`` = un-scored = neutral). A
        deleted doc reads as un-scored."""
        rm = self._spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError:  # pragma: no cover — a candidate chunk's
            return None  # doc was deleted mid-query (cascade keeps them in sync, so defensive)
        assert isinstance(doc, SourceDoc)
        return doc.quality_score

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

    def _dense_order_mem(
        self, chunks: dict[str, DocChunk], vec: list[float], *, field: str
    ) -> list[str]:
        """In-memory dense order for the #328 overlay — nearest-first by cosine
        over the explicit (overlaid) chunk set, capped at ``candidates``. Cosine
        is the store's metric too, so this matches the persisted-vector order."""
        scored: list[tuple[float, str]] = []
        for cid, ch in chunks.items():
            v = ch.embedding if field == "embedding" else ch.embedding_alt
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
        return [
            r.info.resource_id  # ty: ignore[unresolved-attribute]
            for r in rm.list_resources(query)
        ]

    def _load_chunks(
        self,
        collection_ids: list[str],
        location: LocationFilter | None = None,
        exclude_doc_ids: frozenset[str] = frozenset(),
    ) -> dict[str, DocChunk]:
        """The chunk universe for BM25 + MMR + passage metadata. A `location`
        filter scopes it the SAME way as the dense query, so every retrieval
        signal sees the same narrowed candidate set."""
        rm = self._spec.get_resource_manager(DocChunk)
        out: dict[str, DocChunk] = {}
        for cid in collection_ids:
            scope = _scoped(QB["collection_id"] == cid, location, exclude_doc_ids)
            for r in rm.list_resources(scope.build()):
                data = r.data
                assert isinstance(data, DocChunk)
                out[r.info.resource_id] = data  # ty: ignore[unresolved-attribute]
        return out

    def _resolve_doc_id(self, chunk: DocChunk) -> str | None:
        """#104 P1 — resolve a chunk to its live SourceDoc id by CONTENT, not by a
        single (deletable) ``source_doc_id``. A COALESCING resolver:

        - When the chunk carries a content ``source_file_id``, return the
          canonical doc sharing that content in the same collection (see
          ``_canonical_doc_id``) — so a dangling / stale ``source_doc_id`` no
          longer drops the hit while the content still lives at a real path.
        - Legacy chunks predating #104 (``source_file_id == ""``), or content
          whose docs are all gone, fall back to the chunk's own ``source_doc_id``.

        Returns the resolved id (its liveness is confirmed by the caller's
        ``_doc_path`` guard) or ``None`` only when there is no id to try."""
        if chunk.source_file_id:
            canon = self._canonical_doc_id(chunk.collection_id, chunk.source_file_id)
            if canon is not None:
                return canon
        return chunk.source_doc_id or None

    def _doc_file_id(self, doc_id: str) -> str:
        """A doc's content hash (``content.file_id``), or ``""`` if the doc is
        gone / unset — used to match its content-addressed chunks (#104)."""
        rm = self._spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError:
            return ""
        assert isinstance(doc, SourceDoc)
        fid = doc.content.file_id
        return fid if isinstance(fid, str) else ""

    def _canonical_doc_id(self, collection_id: str, file_id: str) -> str | None:
        """The canonical live SourceDoc for a piece of content in a collection:
        the earliest-created doc sharing ``(collection_id, content.file_id)``,
        ``resource_id`` as a deterministic tiebreak. ``None`` when the content
        has no live doc (every path deleted) — the chunk is then a true orphan."""
        rm = self._spec.get_resource_manager(SourceDoc)
        best_id: str | None = None
        best_key: tuple[float, str] | None = None
        for r in rm.list_resources(
            ((QB["collection_id"] == collection_id) & (QB["file_id"] == file_id)).build()
        ):
            rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
            key = (r.info.created_time.timestamp(), rid)  # ty: ignore[unresolved-attribute]
            if best_key is None or key < best_key:
                best_key, best_id = key, rid
        return best_id

    def _doc_path(self, doc_id: str) -> str | None:
        """The SourceDoc's stored path (a record field) — for the display
        filename. Never derived by parsing the opaque id. Returns ``None`` when
        the doc is gone: an ORPHAN chunk whose owner was deleted (#104 re-home
        keeps source_doc_id valid, but defend in depth — a dangling ref must drop
        the hit here, not crash the whole search)."""
        rm = self._spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(doc, SourceDoc)
        return doc.path

    def _canonical_text(self, doc_id: str) -> str:
        rm = self._spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError:  # pragma: no cover — chunk implies its doc exists
            return ""
        assert isinstance(doc, SourceDoc)
        # Chunk offsets index into the parser's extracted text (persisted on
        # `doc.text`), so THAT is the canonical text — never the raw bytes.
        # Decoding `content` for an image / pdf / docx yields binary garbage
        # (U+FFFD) that would poison the LLM's context with gibberish (#114).
        if doc.text is not None:
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
            return _NO_EXTRACTABLE_TEXT
