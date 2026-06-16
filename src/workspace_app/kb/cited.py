"""Citation analytics: record a `CitationEvent` per persisted ``[n]``, and
aggregate the log into collection / doc / chunk "cited" counts.

Counting rule (see `CitationEvent`): each event credits its doc and collection
once and EACH of its merged source chunks once — so ``doc_cited`` is the number
of citations of the doc, NOT the sum of its chunks' counts.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from specstar import QB, SpecStar
from specstar.aggregates import Count

from ..resources import CitationEvent
from ..resources.kb import Citation


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def record_citations(
    spec: SpecStar,
    cites: list[Citation],
    *,
    origin_kind: str,
    origin_id: str,
    cited_by: str,
) -> None:
    """Append one `CitationEvent` per resolved ``[n]`` citation."""
    rm = spec.get_resource_manager(CitationEvent)
    for c in cites:
        rm.create(
            CitationEvent(
                collection_id=c.collection_id,
                document_id=c.document_id,
                source_chunk_ids=list(c.source_chunk_ids),
                origin_kind=origin_kind,
                origin_id=origin_id,
                cited_by=cited_by,
                marker=c.marker,
                created_at=_now_ms(),
            )
        )


def _count_by(spec: SpecStar, field: str) -> dict[str, int]:
    """``{value: count}`` grouped by an indexed CitationEvent field — ONE
    push-down ``exp_aggregate_by`` query that loads only indexed meta (not event
    bodies), instead of scanning the whole append-only log on every list call."""
    rm = spec.get_resource_manager(CitationEvent)
    # exp_aggregate_by is on the concrete ResourceManager, not the interface ty
    # sees (same as event_handlers / message_queue elsewhere).
    rows = rm.exp_aggregate_by(by=QB[field], aggregates={"n": Count()})  # ty: ignore[unresolved-attribute]
    return {r.key: r.n for r in rows}


def collection_cited(spec: SpecStar) -> dict[str, int]:
    """{collection_id: number of citations} — one per event."""
    return _count_by(spec, "collection_id")


def doc_cited(spec: SpecStar) -> dict[str, int]:
    """{document_id: number of citations} — one per event, regardless of how
    many chunks the cited passage merged."""
    return _count_by(spec, "document_id")


def doc_cited_for_ids(spec: SpecStar, document_ids: list[str]) -> dict[str, int]:
    """{document_id: number of citations} scoped to ``document_ids`` — the
    page-sized form of `doc_cited`. A listing renders ≤ N docs of ONE collection,
    so it group-by-counts the citation log filtered to those ids (an indexed
    ``document_id IN (...)`` push-down) instead of aggregating the WHOLE log into
    a global map just to look up the page. Empty input ⇒ no query (``{}``)."""
    if not document_ids:
        return {}
    rm = spec.get_resource_manager(CitationEvent)
    rows = rm.exp_aggregate_by(  # ty: ignore[unresolved-attribute]
        by=QB["document_id"],
        aggregates={"n": Count()},
        query=(QB["document_id"].in_(document_ids)).build(),
    )
    return {r.key: r.n for r in rows}


def doc_cited_count(spec: SpecStar, document_id: str) -> int:
    """The cited count for ONE document — a counted query, not the whole dict
    (for the single-doc render path)."""
    rm = spec.get_resource_manager(CitationEvent)
    return rm.count_resources((QB["document_id"] == document_id).build())


def chunk_cited(spec: SpecStar, document_id: str) -> dict[str, int]:
    """{chunk_id: number of citations} for ONE document's chunks — +1 for each
    source chunk of each of that doc's events. ``source_chunk_ids`` is a list
    field, and specstar has no group-by over a list's elements (``exp_aggregate_by``
    keys the whole list as one group; specstar discussion #360 §2). ``.contains``
    is only a membership *filter*, not a group-by, so it can't produce
    ``{chunk_id: count}`` either. So we scope to the doc's events via the indexed
    ``document_id`` filter and fan out the per-chunk tally in Python — bounded by
    one doc's citations, not the whole log (#360 "Option B"). If this ever becomes
    a hot read path, denormalise into a per-(event, chunk) side resource for a
    scalar group-by (#360 "Option A")."""
    rm = spec.get_resource_manager(CitationEvent)
    c: Counter[str] = Counter()
    for r in rm.list_resources((QB["document_id"] == document_id).build()):
        d = r.data
        assert isinstance(d, CitationEvent)
        for cid in d.source_chunk_ids:
            c[cid] += 1
    return dict(c)
