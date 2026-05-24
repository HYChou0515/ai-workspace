"""Retriever — hybrid retrieval over a set of collections.

v1 pipeline: dense (in-process cosine over the stored chunk vectors) + sparse
(BM25) → Reciprocal Rank Fusion → MMR diversification → parent-document merge →
top-k RetrievedPassage. The Embedder is injected (asymmetric query embedding);
the LLM-driven enhancements (multi-query, HyDE, rerank) layer on top in later
slices via an optional `llm`.

(v2 will push the dense search down into specstar's vector query for scale; v1
loads a collection's chunks and scores them in-process, which is simple, uses
the stored vectors, and works uniformly across multiple collections.)
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
from .llm import Llm
from .merge import ScoredChunk, merge_passages
from .query import expand_queries, hypothetical_document
from .rerank import rerank_passages


class Retriever:
    def __init__(
        self,
        spec: SpecStar,
        *,
        embedder: Embedder,
        llm: Llm | None = None,
        candidates: int = 20,
        top_k: int = 5,
    ) -> None:
        self._spec = spec
        self._embedder = embedder
        self._llm = llm
        self._candidates = candidates
        self._top_k = top_k

    def search(self, query: str, collection_ids: list[str]) -> list[RetrievedPassage]:
        chunks = self._load_chunks(collection_ids)  # {chunk_id: DocChunk}
        if not chunks:
            return []

        # Multi-query: an LLM (when given) widens recall with alternative phrasings;
        # each variant contributes a dense + a sparse ranked list to the fusion.
        queries = expand_queries(self._llm, query) if self._llm is not None else [query]
        corpus = [(cid, ch.text) for cid, ch in chunks.items()]
        ranked_lists: list[list[str]] = []
        for q in queries:
            qv = self._embedder.embed_query(q)
            ranked_lists.append(self._dense_order(chunks, qv))
            ranked_lists.append(bm25_rank(q, corpus))

        # HyDE: embed a hypothetical answer (as a pseudo-document) and add it as
        # one more dense probe, so we also match answer-shaped passages.
        if self._llm is not None:
            hyde = hypothetical_document(self._llm, query)
            if hyde:
                hv = self._embedder.embed_documents([hyde])[0]
                ranked_lists.append(self._dense_order(chunks, hv))

        fused = reciprocal_rank_fusion(ranked_lists)[: self._candidates]

        # RRF order → descending relevance scores; MMR for diversity (cosine of
        # the stored chunk vectors as the similarity).
        relevance = {cid: 1.0 / (rank + 1) for rank, cid in enumerate(fused)}
        order = mmr(
            fused,
            relevance=relevance,
            similarity=lambda a, b: 1.0 - cosine_distance(chunks[a].embedding, chunks[b].embedding),
            k=self._top_k * 3,
        )

        scored = [
            ScoredChunk(
                chunk_id=cid,
                document_id=chunks[cid].source_doc_id,
                collection_id=chunks[cid].collection_id,
                filename=posixpath.basename(chunks[cid].source_doc_id),
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
            passages = rerank_passages(self._llm, query, passages)
        return passages[: self._top_k]

    def _dense_order(self, chunks: dict[str, DocChunk], vec: list[float]) -> list[str]:
        """Chunk ids ordered nearest-first to `vec` by cosine distance."""
        return sorted(chunks, key=lambda cid: cosine_distance(vec, chunks[cid].embedding))

    def _load_chunks(self, collection_ids: list[str]) -> dict[str, DocChunk]:
        rm = self._spec.get_resource_manager(DocChunk)
        out: dict[str, DocChunk] = {}
        for cid in collection_ids:
            for r in rm.list_resources((QB["collection_id"] == cid).build()):
                data = r.data
                assert isinstance(data, DocChunk)
                out[r.info.resource_id] = data  # ty: ignore[unresolved-attribute]
        return out

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
