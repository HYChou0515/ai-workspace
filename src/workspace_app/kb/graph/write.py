"""Persist a doc's metric claims (#534 slice 1).

Idempotent per-doc: re-extracting a doc WIPES its existing GraphClaims then
writes fresh, so tuning the prompt and re-running never double-counts. Entity
resolution / cross-doc dedup is a later slice — this is a flat write.
"""

from __future__ import annotations

from collections.abc import Iterable

from specstar import QB, SpecStar

from ...resources.graph import GraphClaim
from ..llm import ILlm
from .extract import extract_claims


def norm_metric(metric: str) -> str:
    """Stable grouping key for a metric surface form: collapse whitespace +
    casefold. (VALUE normalisation — parsing "1.2M" into a number — is a separate,
    later concern; this only canonicalises the NAME for filter / group.)"""
    return " ".join(metric.split()).casefold()


def write_doc_claims(
    spec: SpecStar,
    llm: ILlm,
    *,
    collection_id: str,
    source_doc_id: str,
    chunks: Iterable[tuple[str, str]],
) -> int:
    """Extract + idempotently persist one doc's metric claims. ``chunks`` is
    ``(chunk_id, text)`` pairs. Wipes the doc's existing GraphClaims first (a
    metas-only delete), then writes fresh. Returns the number written."""
    rm = spec.get_resource_manager(GraphClaim)
    # Hard-delete (not soft): a soft ``delete`` still shows in list_resources, so
    # a re-run would accumulate. permanently_delete wipes for real.
    stale = [
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources((QB["source_doc_id"] == source_doc_id).build())
    ]
    for rid in stale:
        rm.permanently_delete(rid)
    written = 0
    for chunk_id, text in chunks:
        for claim in extract_claims(llm, text):
            rm.create(
                GraphClaim(
                    collection_id=collection_id,
                    source_doc_id=source_doc_id,
                    chunk_id=chunk_id,
                    norm_metric=norm_metric(claim.metric),
                    metric=claim.metric,
                    value=claim.value,
                    period=claim.period,
                    unit=claim.unit,
                )
            )
            written += 1
    return written
