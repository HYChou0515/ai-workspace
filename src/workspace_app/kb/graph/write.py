"""Persist a doc's attribute statements (#534 slice 1, re-cut by #630).

Idempotent per-doc: re-extracting a doc WIPES its existing GraphClaims then
writes fresh, so tuning the prompt and re-running never double-counts. Entity
resolution / cross-doc dedup is a later slice — this is a flat write.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ...resources.graph import GraphClaim
from ..doc_permission import doc_mirror_fields
from .entity_extract import Extraction
from .normalize import norm_attribute as _norm_attribute
from .normalize import norm_period, norm_surface, norm_unit

_LOGGER = logging.getLogger(__name__)


def wipe_doc_claims(spec: SpecStar, source_doc_id: str) -> int:
    """Drop every claim extracted from one deck. Returns how many went.

    Called on the re-extraction path (wipe then rewrite, so tuning the prompt and
    re-running never double-counts) AND whenever the deck itself is torn down. A
    claim is keyed on its deck — unlike a chunk, which #104 made content-addressed
    and therefore refcounted — so there is no shared-content question here: the
    deck goes, its numbers go. An orphan would otherwise keep whatever mirror it
    last held (readable), and no fan-out keyed on a live doc could ever reach it
    again.

    Hard-delete, not soft: a soft ``delete`` still shows up in ``list_resources``,
    so a re-run would accumulate.
    """
    rm = spec.get_resource_manager(GraphClaim)
    stale = [
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources((QB["source_doc_id"] == source_doc_id).build())
    ]
    for rid in stale:
        rm.permanently_delete(rid)
    return len(stale)


def write_doc_claims(
    spec: SpecStar,
    extracted: Iterable[tuple[str, Extraction]],
    *,
    collection_id: str,
    source_doc_id: str,
) -> int:
    """Idempotently persist one doc's attribute statements from an ALREADY-
    extracted pass — ``(chunk_id, Extraction)`` pairs (#630 P4: one model call
    per chunk feeds both layers). Wipes the doc's existing GraphClaims first (a
    metas-only delete), then writes fresh. Returns the number written."""
    rm = spec.get_resource_manager(GraphClaim)
    wipe_doc_claims(spec, source_doc_id)
    # #534 slice 2: stamp the deck's effective read permission onto every claim.
    # Read ONCE per doc — it can't change mid-extraction in any way this write
    # could honour, and the permission fan-out re-pushes it if it does. Skipping it
    # would not merely lose the filter: the claim scope treats an unwritten mirror
    # as invisible, so the claims would be born unreadable.
    #
    # A doc that no longer exists is NOT an error here: #104 made a chunk
    # content-addressed rather than bound to a deletable doc, so chunks outlive
    # their deck. A vanished deck has no permission to inherit and nothing worth
    # extracting, and one dangling doc must not fail the batch its neighbours ride
    # in. The wipe above has already cleared what it left behind.
    try:
        mirror = doc_mirror_fields(spec, source_doc_id)
    except ResourceIDNotFoundError:
        _LOGGER.warning(
            "graph: doc %s is gone; wiped its claims and skipped extraction", source_doc_id
        )
        return 0
    written = 0
    for chunk_id, extraction in extracted:
        for claim in extraction.attributes:
            rm.create(
                GraphClaim(
                    collection_id=collection_id,
                    source_doc_id=source_doc_id,
                    chunk_id=chunk_id,
                    norm_subject=norm_surface(claim.subject),
                    subject=claim.subject,
                    norm_attribute=_norm_attribute(claim.attribute),
                    attribute=claim.attribute,
                    value=claim.value,
                    norm_value=norm_surface(claim.value),
                    period=claim.period,
                    norm_period=norm_period(claim.period),
                    unit=claim.unit,
                    norm_unit=norm_unit(claim.unit),
                    **mirror,
                )
            )
            written += 1
    return written
