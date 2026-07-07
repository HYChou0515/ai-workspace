"""Per-document chunk counts for the documents list (issue #103).

The documents-list page shows each doc's chunk count. Counting by streaming
every matching `DocChunk` row into Python materialises whole chunk bodies
(text + embedding vectors) just to tally them — the dominant cost of a slow
listing. Instead group-by-count the chunks scoped to the page's docs: a
single-key `Count`-only `exp_aggregate_by` that specstar pushes down to the
store engine as a real ``GROUP BY`` (loading only indexed meta), mirroring
`cited.doc_cited_for_ids`.

#104: a chunk is bound to CONTENT (``source_file_id``), not to one doc, so a
doc's count is ``count(chunk where source_file_id == doc.content.file_id)`` —
collection-scoped, since ``file_id`` is a global content hash. Identical content
at several paths shares ONE chunk set, so every backed doc reports the shared
count (not 0). A legacy pre-#104 chunk has ``source_file_id == ""`` (no content
key), so it is tallied via ``source_doc_id`` instead — keeping the existing
corpus's counts correct through the deploy→reindex window before file_ids are
stamped (a chunk is in exactly one of the two buckets, never double-counted).
"""

from __future__ import annotations

from specstar import QB, SpecStar
from specstar.aggregates import Count

from ..resources.kb import DocChunk


def doc_chunks_for_ids(
    spec: SpecStar, collection_id: str, doc_file_ids: dict[str, str]
) -> dict[str, int]:
    """``{doc_id: chunk count}`` for a page's docs — ``doc_file_ids`` maps each
    rendered doc id to its ``content.file_id`` (``""`` when unknown / legacy).
    Two scoped, indexed ``GROUP BY`` push-downs (never a chunk-body scan): one by
    ``source_file_id`` for content-addressed chunks, one by ``source_doc_id`` for
    legacy ``source_file_id == ""`` chunks. Only positive counts are returned
    (#103). Empty input ⇒ no query (``{}``)."""
    if not doc_file_ids:
        return {}
    rm = spec.get_resource_manager(DocChunk)
    doc_ids = list(doc_file_ids)
    file_ids = sorted({f for f in doc_file_ids.values() if f})
    # Content-addressed counts, collection-scoped (file_id is a global hash). The
    # aggregate is on the concrete ResourceManager, not the interface ty sees.
    by_content: dict[str, int] = {}
    if file_ids:
        content_rows = rm.exp_aggregate_by(  # ty: ignore[unresolved-attribute]
            by=QB["source_file_id"],
            aggregates={"n": Count()},
            query=(
                (QB["collection_id"] == collection_id) & (QB["source_file_id"].in_(file_ids))
            ).build(),
        )
        by_content = {r.key: r.n for r in content_rows}
    # Legacy fallback: pre-#104 chunks (source_file_id == "") tallied by doc id.
    legacy_rows = rm.exp_aggregate_by(  # ty: ignore[unresolved-attribute]
        by=QB["source_doc_id"],
        aggregates={"n": Count()},
        query=((QB["source_doc_id"].in_(doc_ids)) & (QB["source_file_id"] == "")).build(),
    )
    by_legacy = {r.key: r.n for r in legacy_rows}
    out: dict[str, int] = {}
    for doc_id, fid in doc_file_ids.items():
        n = (by_content.get(fid, 0) if fid else 0) + by_legacy.get(doc_id, 0)
        if n:
            out[doc_id] = n
    return out
