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
from .fusion import mmr, reciprocal_rank_fusion
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


def _scoped(base: ConditionBuilder, location: LocationFilter | None) -> ConditionBuilder:
    """AND the location filter's predicates onto a base query. `None`/empty ⇒
    the base unchanged (unscoped)."""
    if location is None:
        return base
    out = base
    for cond in location.conditions():
        out = out & cond
    return out


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

    def search(
        self,
        query: str,
        collection_ids: list[str],
        on_progress: OnChunk | None = None,
        *,
        enhancements: Enhancements | None = None,
        location: LocationFilter | None = None,
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
        empty filter) ⇒ unscoped, exactly as before."""
        loc = location if location is not None and not location.is_empty() else None
        chunks = self._load_chunks(collection_ids, loc)  # {chunk_id: DocChunk}
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
                self._dense_order(collection_ids, qv, field="embedding", location=loc)
            )
            # P3.0 fan-out: a separate dense pass for the code-vector field
            # (when wired). Each field uses its own embedder, so the query
            # vector is in the right geometry.
            if self._code_embedder is not None:
                qv_alt = self._code_embedder.embed_query(q)
                ranked_lists.append(
                    self._dense_order(collection_ids, qv_alt, field="embedding_alt", location=loc)
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
                    self._dense_order(collection_ids, hv, field="embedding", location=loc)
                )
                if self._code_embedder is not None:
                    hv_alt = self._code_embedder.embed_documents([doc])[0]
                    ranked_lists.append(
                        self._dense_order(
                            collection_ids, hv_alt, field="embedding_alt", location=loc
                        )
                    )

        fused = reciprocal_rank_fusion(ranked_lists)[: self._candidates]

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
            k=self._top_k * 3,
        )

        # The display filename is the basename of the SourceDoc's stored path —
        # the id is opaque and must not be parsed for it.
        scored = [
            ScoredChunk(
                chunk_id=cid,
                document_id=chunks[cid].source_doc_id,
                collection_id=chunks[cid].collection_id,
                filename=posixpath.basename(self._doc_path(chunks[cid].source_doc_id)),
                seq=chunks[cid].seq,
                start=chunks[cid].start,
                end=chunks[cid].end,
                score=relevance[cid],
                provenance=chunks[cid].provenance,
            )
            for cid in order
        ]
        passages = merge_passages(scored, text_of=self._canonical_text)
        # Final LLM rerank over the merged passages — bool knob.
        if resolved.rerank:
            assert self._llm is not None
            step("\n↻ rerank\n")
            passages = rerank_passages(self._llm, query, passages, on_progress=on_progress)
        return passages[: self._top_k]

    def _dense_order(
        self,
        collection_ids: list[str],
        vec: list[float],
        *,
        field: str = "embedding",
        location: LocationFilter | None = None,
    ) -> list[str]:
        """Top candidate chunk ids nearest `vec` in the given vector `field`
        (``embedding`` or ``embedding_alt``), via specstar's native vector
        query (pgvector-indexed when available; computed in-store otherwise).
        Ordered nearest-first by cosine distance. A `location` filter (#263)
        AND-narrows the candidate set on the indexed provenance fields BEFORE
        the vector sort — index filter + vector order in ONE query, the same
        way `collection_id` already scopes it."""
        rm = self._spec.get_resource_manager(DocChunk)
        scope = _scoped(QB["collection_id"].in_(collection_ids), location)
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
        self, collection_ids: list[str], location: LocationFilter | None = None
    ) -> dict[str, DocChunk]:
        """The chunk universe for BM25 + MMR + passage metadata. A `location`
        filter scopes it the SAME way as the dense query, so every retrieval
        signal sees the same narrowed candidate set."""
        rm = self._spec.get_resource_manager(DocChunk)
        out: dict[str, DocChunk] = {}
        for cid in collection_ids:
            scope = _scoped(QB["collection_id"] == cid, location)
            for r in rm.list_resources(scope.build()):
                data = r.data
                assert isinstance(data, DocChunk)
                out[r.info.resource_id] = data  # ty: ignore[unresolved-attribute]
        return out

    def _doc_path(self, doc_id: str) -> str:
        """The SourceDoc's stored path (a record field) — for the display
        filename. Never derived by parsing the opaque id."""
        rm = self._spec.get_resource_manager(SourceDoc)
        doc = rm.get(doc_id).data
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
