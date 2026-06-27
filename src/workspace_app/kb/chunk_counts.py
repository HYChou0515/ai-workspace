"""Per-document chunk counts for the documents list (issue #103).

The documents-list page shows each doc's chunk count. Counting by streaming
every matching `DocChunk` row into Python materialises whole chunk bodies
(text + embedding vectors) just to tally them — the dominant cost of a slow
listing. Instead group-by-count the chunks scoped to the page's doc ids: a
single-key `Count`-only `exp_aggregate_by` that specstar pushes down to the
store engine as a real ``GROUP BY`` (loading only indexed meta), mirroring
`cited.doc_cited_for_ids`.
"""

from __future__ import annotations

from specstar import QB, SpecStar
from specstar.aggregates import Count

from ..resources.kb import DocChunk


def doc_chunks_for_ids(spec: SpecStar, document_ids: list[str]) -> dict[str, int]:
    """{source_doc_id: chunk count} scoped to ``document_ids`` — the page-sized
    chunk tally. A listing renders ≤ N docs of ONE collection, so it
    group-by-counts `DocChunk` filtered to those ids (an indexed
    ``source_doc_id IN (...)`` push-down) instead of scanning / materialising
    every chunk in the store. Empty input ⇒ no query (``{}``)."""
    if not document_ids:
        return {}
    rm = spec.get_resource_manager(DocChunk)
    # exp_aggregate_by is on the concrete ResourceManager, not the interface ty
    # sees (same as in `cited.doc_cited_for_ids`).
    rows = rm.exp_aggregate_by(  # ty: ignore[unresolved-attribute]
        by=QB["source_doc_id"],
        aggregates={"n": Count()},
        query=(QB["source_doc_id"].in_(document_ids)).build(),
    )
    return {r.key: r.n for r in rows}
