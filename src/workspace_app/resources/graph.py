"""Knowledge-graph resources (#534).

Slice 1 ships one flat table, ``GraphClaim`` — an extracted metric measurement.
Later slices add the full Graph* family (GraphEntity / GraphMention /
GraphRelationship / GraphSummary) + entity resolution.
"""

from __future__ import annotations

from typing import Annotated

from msgspec import Struct
from specstar import OnDelete, Ref


class GraphClaim(Struct):  # → resource "graph-claim"
    """One extracted metric measurement — flat, queryable evidence (#534 slice 1).

    Collection-scoped (permission inherits like SourceDoc / DocChunk via the
    cascade ``Ref``); ``source_doc_id`` / ``chunk_id`` are the provenance back to
    the source deck/slide. ``value`` is stored VERBATIM ("1.2M", "15%") —
    normalisation (parse → number + unit) is a later, app-side concern.

    Indexed: ``collection_id`` (Ref, auto), ``norm_metric`` + ``period`` (filter a
    metric's values across decks and, later, rollup grouping), ``source_doc_id``
    (so a re-extraction can wipe + rewrite one doc's claims). ``norm_metric`` is
    the server-computed normalised key; ``metric`` keeps the raw surface form for
    display.
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    source_doc_id: str  # provenance: the deck/doc (indexed; NOT a cascade Ref)
    norm_metric: str  # normalised metric key (indexed) — filter / group on this
    metric: str  # raw surface form (display)
    value: str  # verbatim value ("1.2M", "15%")
    period: str = ""  # indexed; "" when the metric carries no period
    unit: str = ""
    chunk_id: str = ""  # provenance: the chunk / slide
    confidence: float = 1.0
