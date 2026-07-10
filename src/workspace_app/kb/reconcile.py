"""#506 P6 reconcile — assign a ``cluster_key`` to a card-generation candidate.

A candidate joins an existing cluster **deterministically** when it shares an
exact ``norm_key`` with a prior member (no embedding needed — an upload burst of
the same surface form all lands in one cluster, race-free); else by **cosine
nearest-neighbour** when a member is within the similarity threshold ``tau``;
else it opens a brand-new cluster keyed by its own ``norm_key``. The assigned
``cluster_key`` is what the review inbox groups by, so semantically-equal
candidates from different runs collapse to one row (⑤).
"""

from __future__ import annotations

from dataclasses import dataclass

from specstar import QB, SpecStar
from specstar.util.vector_distance import cosine_distance

from ..resources.kb import ClusterMember


def assign_cluster_key(
    spec: SpecStar,
    *,
    collection_id: str,
    norm_key: str,
    embedding: list[float],
    tau: float,
) -> str:
    """Return the ``cluster_key`` a new candidate should join.

    ``tau`` is a cosine SIMILARITY threshold in ``[0, 1]``: the nearest member is
    adopted only when ``similarity >= tau`` (``similarity = 1 - cosine_distance``).
    Exact ``norm_key`` overlap short-circuits the vector query and wins regardless
    of ``tau`` — it is the deterministic identity."""
    rm = spec.get_resource_manager(ClusterMember)
    # Deterministic exact-key fast path — same surface form ⇒ same concept.
    if norm_key:
        exact = ((QB["collection_id"] == collection_id) & (QB["norm_key"] == norm_key)).build()
        for r in rm.list_resources(exact):
            m = r.data
            assert isinstance(m, ClusterMember)
            return m.cluster_key or norm_key
    # Semantic nearest-neighbour — a different surface form for the same concept.
    near = (
        (QB["collection_id"] == collection_id)
        # specstar's order_by type union omits VectorDistanceSort (works at runtime)
        .order_by(QB["embedding"].cosine(embedding).asc())  # ty: ignore[invalid-argument-type]
        .limit(1)
        .build()
    )
    for r in rm.list_resources(near):
        m = r.data
        assert isinstance(m, ClusterMember)
        if m.embedding is not None and 1.0 - cosine_distance(m.embedding, embedding) >= tau:
            return m.cluster_key or m.norm_key
    return norm_key


@dataclass(frozen=True)
class Grade:
    """The reconcile verdict for one candidate against the collection's existing
    cards + wiki. ``action`` is ``suppress`` (already explained → auto-drop, kept
    only as an auditable ClusterMember), ``update`` (partially covered → suggest
    editing ``target_card_id``), or ``new`` (a genuinely new concept). ``reason``
    records WHY a suppress fired (``wiki`` grep hit vs ``near-card``) for the audit
    view."""

    action: str  # "suppress" | "update" | "new"
    target_card_id: str | None = None
    reason: str = ""  # "wiki" | "near-card" | ""


def grade_candidate(
    spec: SpecStar,
    *,
    collection_id: str,
    embedding: list[float],
    tau_high: float,
    tau_update: float,
    wiki_hit: bool = False,
) -> Grade:
    """Decide a candidate's fate against what the collection ALREADY explains.

    A wiki grep hit is a deterministic "already covered" signal → ``suppress``.
    Otherwise the nearest existing CARD member decides by cosine similarity:
    ``>= tau_high`` → ``suppress`` (a semantic duplicate), ``>= tau_update`` →
    ``update`` that card (related but adds something), else ``new``. This is the
    semantic layer over the exact-key ``classify_against_existing`` (#175)."""
    if wiki_hit:
        return Grade("suppress", reason="wiki")
    rm = spec.get_resource_manager(ClusterMember)
    near_card = (
        ((QB["collection_id"] == collection_id) & (QB["kind"] == "card"))
        # specstar's order_by type union omits VectorDistanceSort (works at runtime)
        .order_by(QB["embedding"].cosine(embedding).asc())  # ty: ignore[invalid-argument-type]
        .limit(1)
        .build()
    )
    for r in rm.list_resources(near_card):
        m = r.data
        assert isinstance(m, ClusterMember)
        if m.embedding is None:
            break
        sim = 1.0 - cosine_distance(m.embedding, embedding)
        if sim >= tau_high:
            return Grade("suppress", target_card_id=m.ref_id, reason="near-card")
        if sim >= tau_update:
            return Grade("update", target_card_id=m.ref_id, reason="near-card")
        break
    return Grade("new")
