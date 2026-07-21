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

    #534 slice 2: the ``collection_*`` / ``doc_*`` fields are a denormalized
    mirror of the READ permission of the deck this claim came from, so
    ``graph_claim_access_scope`` can hide the row at the storage layer, which is
    where the auto-CRUD read routes are covered without a hand-written guard on
    each. (``GET /{model}/export`` is the exception — it bypasses access scopes for
    every model in the app, ``source-doc`` and ``collection`` included, so it is a
    pre-existing hole this does not close.)
    They are written by the extractor and re-pushed by the permission fan-out;
    nothing else may set them. The mirror is the doc's EFFECTIVE permission, both
    layers: its collection's (#303) and its own tightening (#308).
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    source_doc_id: str  # provenance: the deck/doc (indexed; NOT a cascade Ref)
    norm_metric: str  # normalised metric key (indexed) — filter / group on this
    metric: str  # raw surface form (display)
    value: str  # verbatim value ("1.2M", "15%")
    period: str = ""  # raw surface form (display)
    unit: str = ""  # raw surface form (display)
    # #534 甲: the comparison keys, DERIVED from the raw surfaces above by the pure
    # rules in `kb/graph/normalize.py` and stored because they must be INDEXED —
    # `exp_aggregate_by` groups on `indexed_data`, so a key that only existed at
    # read time could not be grouped on at all, and a filter on it would silently
    # match nothing. The raw surface stays beside each key as the thing a person
    # reads and the thing a re-derivation starts from.
    #
    # A derived-and-stored value is STATE, so it is versioned like state: changing
    # a rule bumps the `Schema` version and the migration step carries the new
    # algorithm (see `resources/__init__.py`). Every row then records WHICH version
    # of the rules produced its keys, which is what keeps an improved rule from
    # leaving older rows quietly on the old one.
    norm_period: str = ""
    norm_unit: str = ""
    chunk_id: str = ""  # provenance: the chunk / slide
    confidence: float = 1.0
    # --- #534 slice 2: the read-permission mirror (see the class docstring) ---
    # BOTH grant lists ride along at BOTH levels, because reading a claim needs
    # both answers: `read_meta` ("may you know this deck exists") and
    # `read_content` ("may you read it"). The lists are independent, and
    # "discoverable but not readable" is a state the product models on purpose —
    # so a claim, which IS content, needs the content grant, and a claim must not
    # become the one way to learn about a deck you cannot see.
    collection_visibility: str = ""  # the parent collection's visibility (#303)
    collection_read_meta: list[str] = []
    collection_read_content: list[str] = []
    collection_created_by: str = ""  # its owner — the authority every half matches
    # The deck's OWN verdict (#308), stated explicitly: a deck that adds no
    # restriction mirrors "public". "" is NOT "no override" — it is "no mirror was
    # ever written", which the scope reads as invisible. The default has to fail
    # CLOSED: a writer that forgets the mirror then loses rows (loud, someone
    # chases it) instead of publishing them (silent, nobody reports a leak).
    doc_visibility: str = ""
    doc_read_meta: list[str] = []
    doc_read_content: list[str] = []
