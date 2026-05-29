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

from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError
from specstar.util.vector_distance import cosine_distance

from ..resources.kb import DocChunk, RetrievedPassage, SourceDoc
from .bm25 import bm25_rank
from .embedder import Embedder
from .fusion import mmr, reciprocal_rank_fusion
from .ingest import normalize_text
from .llm import ILlm, OnChunk
from .merge import ScoredChunk, merge_passages
from .query import expand_queries, hypothetical_document
from .rerank import rerank_passages


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

    def search(
        self, query: str, collection_ids: list[str], on_progress: OnChunk | None = None
    ) -> list[RetrievedPassage]:
        chunks = self._load_chunks(collection_ids)  # {chunk_id: DocChunk}
        if not chunks:
            return []

        def step(label: str) -> None:
            if on_progress is not None:
                on_progress(label, False)  # a step header (not model reasoning)

        # Multi-query: an LLM (when given) widens recall with alternative phrasings;
        # each variant contributes a dense + a sparse ranked list to the fusion.
        if self._llm is not None:
            step("\n↻ expanding query\n")
            queries = expand_queries(self._llm, query, on_progress=on_progress)
        else:
            queries = [query]
        corpus = [(cid, ch.text) for cid, ch in chunks.items()]
        ranked_lists: list[list[str]] = []
        for q in queries:
            qv = self._embedder.embed_query(q)
            ranked_lists.append(self._dense_order(collection_ids, qv, field="embedding"))
            # P3.0 fan-out: a separate dense pass for the code-vector field
            # (when wired). Each field uses its own embedder, so the query
            # vector is in the right geometry.
            if self._code_embedder is not None:
                qv_alt = self._code_embedder.embed_query(q)
                ranked_lists.append(
                    self._dense_order(collection_ids, qv_alt, field="embedding_alt")
                )
            ranked_lists.append(bm25_rank(q, corpus))

        # HyDE: embed a hypothetical answer (as a pseudo-document) and add it as
        # one more dense probe, so we also match answer-shaped passages.
        if self._llm is not None:
            step("\n↻ HyDE\n")
            hyde = hypothetical_document(self._llm, query, on_progress=on_progress)
            if hyde:
                hv = self._embedder.embed_documents([hyde])[0]
                ranked_lists.append(self._dense_order(collection_ids, hv, field="embedding"))
                if self._code_embedder is not None:
                    hv_alt = self._code_embedder.embed_documents([hyde])[0]
                    ranked_lists.append(
                        self._dense_order(collection_ids, hv_alt, field="embedding_alt")
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
            )
            for cid in order
        ]
        passages = merge_passages(scored, text_of=self._canonical_text)
        # Final LLM rerank over the merged passages (when an LLM is wired).
        if self._llm is not None:
            step("\n↻ rerank\n")
            passages = rerank_passages(self._llm, query, passages, on_progress=on_progress)
        return passages[: self._top_k]

    def _dense_order(
        self, collection_ids: list[str], vec: list[float], *, field: str = "embedding"
    ) -> list[str]:
        """Top candidate chunk ids nearest `vec` in the given vector `field`
        (``embedding`` or ``embedding_alt``), via specstar's native vector
        query (pgvector-indexed when available; computed in-store otherwise).
        Ordered nearest-first by cosine distance."""
        rm = self._spec.get_resource_manager(DocChunk)
        query = (
            QB["collection_id"]
            .in_(collection_ids)
            # specstar's order_by type union omits VectorDistanceSort (works at runtime)
            .order_by(QB[field].cosine(vec).asc())  # ty: ignore[invalid-argument-type]
            .limit(self._candidates)
            .build()
        )
        return [
            r.info.resource_id  # ty: ignore[unresolved-attribute]
            for r in rm.list_resources(query)
        ]

    def _load_chunks(self, collection_ids: list[str]) -> dict[str, DocChunk]:
        rm = self._spec.get_resource_manager(DocChunk)
        out: dict[str, DocChunk] = {}
        for cid in collection_ids:
            for r in rm.list_resources((QB["collection_id"] == cid).build()):
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
        raw = rm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)
        return normalize_text(raw.decode("utf-8", errors="replace"))
