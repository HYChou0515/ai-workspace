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


def _events(spec: SpecStar) -> list[CitationEvent]:
    rm = spec.get_resource_manager(CitationEvent)
    out: list[CitationEvent] = []
    for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        d = r.data
        assert isinstance(d, CitationEvent)  # the manager yields CitationEvent
        out.append(d)
    return out


def collection_cited(spec: SpecStar) -> dict[str, int]:
    """{collection_id: number of citations} — one per event."""
    c: Counter[str] = Counter()
    for e in _events(spec):
        c[e.collection_id] += 1
    return dict(c)


def doc_cited(spec: SpecStar) -> dict[str, int]:
    """{document_id: number of citations} — one per event, regardless of how
    many chunks the cited passage merged."""
    c: Counter[str] = Counter()
    for e in _events(spec):
        c[e.document_id] += 1
    return dict(c)


def chunk_cited(spec: SpecStar) -> dict[str, int]:
    """{chunk_id: number of citations} — +1 for each source chunk of each event
    (overlap/merge can credit several chunks per doc citation)."""
    c: Counter[str] = Counter()
    for e in _events(spec):
        for cid in e.source_chunk_ids:
            c[cid] += 1
    return dict(c)
