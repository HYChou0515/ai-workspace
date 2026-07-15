"""ClusterMember access layer (#511 P4).

:class:`~workspace_app.resources.kb.ClusterMember` is the reconcile projection
table (#506 P6/P7): every card-gen candidate (proposal / term-question) and each
existing card is projected here with a text ``embedding`` + a ``cluster_key`` so
the review inbox can group one row per concept. reconcile.py owns *creating*
members; this module owns the two things the review lifecycle needs AFTER creation:

1. :func:`set_member_state` — de-join a member when its source resolves. The
   ``state`` field (``active`` / ``inactive`` / ``suppressed``) mirrors the source
   row's lifecycle so the grouped view can ``GROUP BY`` over *only the active*
   members without re-checking each source. #506 never wired this (a committed /
   answered source left its member ``active``); P4 syncs it from the decision
   writes, which is the precondition for the native aggregation below to be correct.
2. :func:`page_clusters` — the grouped view's native, paginated ``GROUP BY
   cluster_key`` (P4), replacing the load-every-item-then-group "fake pagination".
3. :func:`list_members` — the ONE way the review path reads members: projected to
   :data:`MEMBER_FIELDS`, i.e. every field EXCEPT the 1024-dim ``embedding`` (#508).
   The vector is reconcile's input, never the inbox's; deserializing one per pending
   member made the grouped inbox slow independently of how well it paged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import msgspec
from specstar import QB, SpecStar
from specstar.aggregates import Count, Max
from specstar.types import ResourceIDNotFoundError

from ..resources.kb import ClusterMember

# The two member kinds that form the 待審核 queue's concepts (a `card` member is the
# nearest-neighbour comparison corpus, never a queue row). The grouped view's type
# filter narrows to one of these.
QUEUE_KINDS = ("proposal", "term_question")

# One resolved page member: its resource id (== CardProposal id for a proposal), its
# meta creation epoch, and the member row (kind / ref_id / run_id / collection). Typed
# ClusterMember but carrying the vector-free projection below — a structural subset, so
# every field EXCEPT `embedding` reads normally.
MemberRow = tuple[str, float, ClusterMember]
# One concept on a page: its cluster_key, the newest member's epoch (the sort key),
# and the members grouped under it (newest first).
ClusterRow = tuple[str, float, list[MemberRow]]

# A member whose source is still in the queue vs one that has left it. "suppressed"
# is a THIRD state (reconcile auto-dropped the candidate as already-explained) that
# the decision-driven sync must never overwrite — a suppressed candidate never
# became a queue item, so it has no decision to track.
_ACTIVE = "active"
_INACTIVE = "inactive"
_SUPPRESSED = "suppressed"

# Every ClusterMember field EXCEPT the 1024-dim `embedding` Vector (#508). The vector
# is reconcile's nearest-neighbour input — it assigns the cluster_key and is then
# never read again; the review views want the scalars it grouped. Deserializing one
# vector per pending member is what made the grouped inbox take 60s+, so every read
# on the review path projects the vector away. Listed positively (not "all but one")
# because `partial` takes the fields to KEEP; a new scalar must be added here to be
# readable, while a new heavy field stays out by default.
MEMBER_FIELDS = [
    "/collection_id",
    "/kind",
    "/ref_id",
    "/run_id",
    "/norm_key",
    "/cluster_key",
    "/state",
    "/reason",
    "/label",
]


def list_members(rm: Any, query: Any) -> list[Any]:
    """The review path's ONLY ClusterMember read (#508) — the matching rows projected
    to :data:`MEMBER_FIELDS`, so a member's ``embedding`` is never deserialized.

    ``returns=["data", "info"]`` keeps ``r.info`` (the resource id + the meta creation
    time the grouped page sorts on) while ``partial`` narrows ``r.data``; the result
    is a ``Partial_ClusterMember`` — structurally a ClusterMember minus the vector, so
    callers ``cast`` it rather than ``isinstance``-narrow (it is a distinct type)."""
    return rm.list_resources(query, returns=["data", "info"], partial=MEMBER_FIELDS)


def set_member_state(spec: SpecStar, member_id: str, target_state: str) -> None:
    """Set one ClusterMember's ``state`` by id — the decision-driven de-join (#511
    P4). A no-op when the member doesn't exist (a no-reconciler build projects none,
    so a proposal / description-question has no member) or is already at
    ``target_state``. A ``suppressed`` member is left untouched: it tracks a reconcile
    auto-drop, not a queue decision, so a decision write must not resurrect it.

    A read-then-``create_or_update`` upsert (last-write-wins), the SAME write idiom
    reconcile uses for members — ClusterMember rows are published (not draft), so
    they're re-projected via ``create_or_update``, never ``modify``."""
    rm = spec.get_resource_manager(ClusterMember)
    try:
        res = rm.get(member_id)
    except ResourceIDNotFoundError:
        return  # no member for this source (no-reconciler build / description q)
    member = res.data
    assert isinstance(member, ClusterMember)  # rm is ClusterMember-typed; narrows ty
    if member.state == _SUPPRESSED or member.state == target_state:
        return  # never resurrect a suppressed member; skip a no-op write
    rm.create_or_update(member_id, msgspec.structs.replace(member, state=target_state))


def deactivate_member(spec: SpecStar, member_id: str) -> None:
    """Mark a member ``inactive`` — its source left the 待審核 queue (a proposal was
    committed / rejected, a term-question answered / discarded)."""
    set_member_state(spec, member_id, _INACTIVE)


def member_query(
    collection_ids: Sequence[str],
    state: str = _ACTIVE,
    kinds: Sequence[str] = QUEUE_KINDS,
):
    """The review path's member predicate — all three fields indexed, so the GROUP BY,
    the page-member load and the inbox's cluster join never scan: members in
    ``collection_ids`` of a ``state`` (active queue / inactive history) whose ``kind``
    forms a concept. Returns the BUILDER (callers ``&``-extend / ``sort`` / ``build``)."""
    return (
        QB["collection_id"].in_(list(collection_ids))
        & (QB["state"] == state)
        & QB["kind"].in_(list(kinds))
    )


def count_clusters(
    spec: SpecStar,
    collection_ids: Sequence[str],
    *,
    state: str = _ACTIVE,
    kinds: Sequence[str] = QUEUE_KINDS,
) -> int:
    """The number of DISTINCT concepts (``cluster_key`` groups) in the given
    collections + state — the grouped pager's total / the actionable badge, computed
    at the DB (``exp_count_groups``), never by materializing every group."""
    if not collection_ids:
        return 0
    rm = spec.get_resource_manager(ClusterMember)
    return rm.exp_count_groups(  # ty: ignore[unresolved-attribute]
        QB["cluster_key"], query=member_query(collection_ids, state, kinds).build()
    )


def page_clusters(
    spec: SpecStar,
    collection_ids: Sequence[str],
    *,
    state: str = _ACTIVE,
    kinds: Sequence[str] = QUEUE_KINDS,
    offset: int = 0,
    limit: int | None = None,
) -> tuple[list[ClusterRow], int]:
    """One page of the grouped review view — DISTINCT concepts newest-first, paged
    natively at the DB (#511 P4), plus the full distinct-concept ``total``.

    A native ``GROUP BY cluster_key`` (``exp_aggregate_by`` with a group-level
    ``ORDER BY latest DESC LIMIT/OFFSET``) picks the page's concepts ordered by their
    newest member; a second bounded query loads ONLY those concepts' members (``IN``
    the page's keys). This replaces the load-every-item-then-group "fake pagination":
    the per-page work is O(page), not O(all pending). Returns ``(clusters, total)``
    where each cluster is ``(cluster_key, newest_epoch, [(member_id, epoch, member)])``,
    concepts newest-first and each concept's members newest-first."""
    if not collection_ids:
        return [], 0
    rm = spec.get_resource_manager(ClusterMember)
    base = member_query(collection_ids, state, kinds)
    total = rm.exp_count_groups(QB["cluster_key"], query=base.build())  # ty: ignore[unresolved-attribute]
    groups = rm.exp_aggregate_by(  # ty: ignore[unresolved-attribute]
        QB["cluster_key"],
        {"n": Count(), "latest": Max(QB.created_time())},
        query=base.build(),
        order_by="-latest",  # newest concept first; group key breaks ties deterministically
        offset=offset,
        limit=limit,
    )
    order = [(g.key, g["latest"]) for g in groups]
    page_keys = [key for key, _latest in order]
    members_by_key: dict[str, list[MemberRow]] = {key: [] for key in page_keys}
    if page_keys:
        page_query = (base & QB["cluster_key"].in_(page_keys)).build()
        for r in list_members(rm, page_query):
            member = cast(ClusterMember, r.data)  # projected — every field but the vector
            members_by_key[member.cluster_key].append(
                (r.info.resource_id, r.info.created_time.timestamp(), member)
            )
    out: list[ClusterRow] = []
    for key, latest in order:
        members = sorted(members_by_key[key], key=lambda row: row[1], reverse=True)
        out.append((key, latest.timestamp(), members))
    return out, total


def _decision_state(decision: str, active_decisions: frozenset[str]) -> str:
    """The member state a proposal's ``decision`` implies: ``active`` while the
    proposal is still in the queue (pending / accepted), ``inactive`` once terminal."""
    return _ACTIVE if decision in active_decisions else _INACTIVE
