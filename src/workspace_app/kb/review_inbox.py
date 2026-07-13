"""The global 審核 (review) inbox aggregation (#481).

One place to review everything awaiting a human: the card-gen proposals + the
clarification questions the digest raised, across *every* collection the user may
read — flattened one row per card / question so the FE renders a single filterable
table. There are exactly two pending-review item types (``CardGenRun`` proposals
and ``DocQuestion``); both inherit their permission from the parent collection (no
own ACL), so the whole filter is "which collections can this user see", and each
row carries a ``can_act`` flag (whether the user may WRITE to that collection —
committing a card / answering a question is a write).

Pure aggregation + permission projection; the route (``api.review_inbox_routes``)
only converts to pydantic. Mirrors the storage-layer authorize idiom of
``kb.collections.readable_collection_ids`` (list all, ``authorize`` each).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import msgspec
from specstar import QB

from ..perm import Actor, authorize
from ..resources.kb import ClusterMember, Collection, DocQuestion
from .card_gen import ProposedCard, ensure_proposal_ids, is_active
from .card_gen_run import CardGenRunStore
from .doc_questions import questions_by_status

if TYPE_CHECKING:
    from specstar import SpecStar

# Run statuses whose proposals feed each view (#481). A ``done`` run may hold BOTH
# active (pending) proposals — the pending view — and terminal (committed/rejected)
# ones after a partial review — the history view; resolved runs hold only terminal.
_PENDING_RUN_STATUSES = ["done"]
_RESOLVED_RUN_STATUSES = ["done", "committed", "dismissed"]
_OPEN_QUESTION_STATUSES = ["open"]
_RESOLVED_QUESTION_STATUSES = ["answered", "discarded"]


class ReviewCardItem(msgspec.Struct):
    """One card-gen proposal awaiting (or past) review, with its collection + the
    run it belongs to (the ``run`` column / group key) and whether the user may act."""

    run_id: str
    collection_id: str
    collection_name: str
    can_act: bool
    created_time: float
    card: ProposedCard


class ReviewQuestionItem(msgspec.Struct):
    """One clarification question, with its collection + whether the user may act."""

    qid: str
    collection_id: str
    collection_name: str
    can_act: bool
    created_time: float
    question: DocQuestion


class ReviewCluster(msgspec.Struct):
    """#506 P7: one concept's review row — the proposals + questions the reconcile
    step grouped under one ``cluster_key`` (⑤). A card and a question about the same
    thing collapse here, so a reviewer acts on the concept once. ``created_time`` is
    the newest member (the sort key); ``can_act`` is true if the actor may write to
    any member's collection; ``size`` is the total member count."""

    cluster_key: str
    collection_id: str
    collection_name: str
    can_act: bool
    created_time: float
    cards: list[ReviewCardItem]
    questions: list[ReviewQuestionItem]
    size: int = 0


class SuppressedItem(msgspec.Struct):
    """#506 P7: one candidate the reconcile step auto-dropped as already-explained —
    shown only in the suppressed-audit view so a human can verify nothing was wrongly
    discarded. A dropped candidate is not on any run (it never became a proposal), so
    the audit reads straight from the suppressed ClusterMember: ``label`` (the term /
    title) + ``reason`` (``wiki`` vs ``near-card``) are enough to review it."""

    collection_id: str
    collection_name: str
    kind: str  # "proposal" | "term_question"
    label: str
    cluster_key: str
    reason: str  # "wiki" | "near-card"


class ReviewInbox(msgspec.Struct):
    """One page of the aggregated inbox: card proposals + questions (the current
    slice, each newest-first), plus ``total`` — the full filtered count across both
    streams so the FE can render "showing X of N" and page without loading it all."""

    cards: list[ReviewCardItem]
    questions: list[ReviewQuestionItem]
    total: int = 0
    total_actionable: int = 0
    # #506 P7: populated instead of cards/questions when the caller asks for the
    # clustered view (grouped=True) — one row per concept, paginated by cluster.
    clusters: list[ReviewCluster] = msgspec.field(default_factory=list)
    # #506 P7: the auto-suppressed candidates, only when suppressed=True is asked.
    suppressed: list[SuppressedItem] = msgspec.field(default_factory=list)


def cluster_key_map(spec: SpecStar, collection_ids: list[str]) -> dict[tuple[str, str], str]:
    """Map each inbox-visible ClusterMember's ``(run_id, ref_id)`` to its
    ``cluster_key`` — the join the inbox uses to group items. Only ``active``
    proposal + term_question members participate: ``card`` members are the
    comparison corpus (excluded at query time via the indexed ``kind``) and
    ``suppressed``/``inactive`` members are hidden from the default view.

    Perf (#506): a **metas-only** projection — ``list_resources(partial=...)`` fetches
    ONLY the three join fields, so a member's 1024-dim ``embedding`` Vector is never
    deserialized. Loading every active member's whole blob (the vector included) is
    what made the grouped inbox take 60s+. One batched ``in_`` query over all the
    readable collections (indexed) instead of one round-trip per collection."""
    if not collection_ids:
        return {}
    rm = spec.get_resource_manager(ClusterMember)
    query = (
        QB["collection_id"].in_(collection_ids)
        & (QB["state"] == "active")
        & QB["kind"].in_(["proposal", "term_question"])
    ).build()
    out: dict[tuple[str, str], str] = {}
    for r in rm.list_resources(
        query, returns=["data"], partial=["/run_id", "/ref_id", "/cluster_key"]
    ):
        member = cast(ClusterMember, r.data)  # projected subset — only the 3 join fields
        out[(member.run_id, member.ref_id)] = member.cluster_key
    return out


def suppressed_members(spec: SpecStar, readable: dict[str, _CollCtx]) -> list[SuppressedItem]:
    """The auto-suppressed candidates across the readable collections — the audit
    view (⑥). Reads ``state="suppressed"`` ClusterMembers directly (a dropped
    candidate is on no run); card members can't be suppressed so ``kind`` is always
    proposal / term_question.

    Perf (#506): like :func:`cluster_key_map`, a **metas-only** projection (the audit
    only needs the label / reason / kind / cluster_key, never the ``embedding``
    Vector) over one batched ``in_`` query across the readable collections."""
    if not readable:
        return []
    rm = spec.get_resource_manager(ClusterMember)
    query = (QB["collection_id"].in_(list(readable)) & (QB["state"] == "suppressed")).build()
    out: list[SuppressedItem] = []
    for r in rm.list_resources(
        query,
        returns=["data"],
        partial=["/collection_id", "/kind", "/label", "/norm_key", "/cluster_key", "/reason"],
    ):
        member = cast(ClusterMember, r.data)  # projected subset — no embedding
        ctx = readable.get(member.collection_id)
        if ctx is None:  # pragma: no cover — scoped by the query, but stay defensive
            continue
        out.append(
            SuppressedItem(
                collection_id=member.collection_id,
                collection_name=ctx.name,
                kind=member.kind,
                label=member.label or member.norm_key,
                cluster_key=member.cluster_key,
                reason=member.reason,
            )
        )
    return out


def _item_ref(item: ReviewCardItem | ReviewQuestionItem) -> tuple[str, str]:
    """The ``(run_id, ref_id)`` identity a ClusterMember was recorded under — a
    proposal id ("0"/"1"/…) is only unique WITHIN its run, so the run id is part of
    the key; a question id is globally unique (run_id ``""``)."""
    if isinstance(item, ReviewCardItem):
        return (item.run_id, item.card.id)
    return ("", item.qid)


def group_by_cluster(
    items: list[ReviewCardItem | ReviewQuestionItem],
    cluster_of: dict[tuple[str, str], str],
) -> list[ReviewCluster]:
    """Group flat review items into one row per reconcile ``cluster_key`` (⑤).
    ``cluster_of`` maps an item's ``(run_id, ref_id)`` to its cluster; an item with
    no entry (pre-P6 backlog, or a build with no embedder) falls back to its OWN
    singleton cluster so nothing vanishes from review. Clusters come back newest
    member first (``cluster_key`` breaks ties for determinism)."""
    groups: dict[str, ReviewCluster] = {}
    for item in items:
        ref = _item_ref(item)
        key = cluster_of.get(ref) or f"~{ref[0]}\x00{ref[1]}"  # singleton fallback
        cluster = groups.get(key)
        if cluster is None:
            cluster = ReviewCluster(
                cluster_key=key,
                collection_id=item.collection_id,
                collection_name=item.collection_name,
                can_act=item.can_act,
                created_time=item.created_time,
                cards=[],
                questions=[],
            )
            groups[key] = cluster
        if isinstance(item, ReviewCardItem):
            cluster.cards.append(item)
        else:
            cluster.questions.append(item)
        cluster.size += 1
        cluster.created_time = max(cluster.created_time, item.created_time)
        cluster.can_act = cluster.can_act or item.can_act
    out = list(groups.values())
    out.sort(key=lambda c: (-c.created_time, c.cluster_key))
    return out


class _CollCtx(msgspec.Struct):
    name: str
    can_act: bool


def _row_matches(item: ReviewCardItem | ReviewQuestionItem, needle: str) -> bool:
    """Whether a row's text contains the (already lower-cased) ``needle`` — mirrors
    the FE's free-text filter so server-side ``q`` search matches what the user
    typed: a card's title/body/keys, a question's term/text/quote."""
    if isinstance(item, ReviewCardItem):
        c = item.card
        haystack = [c.title, c.body, *c.keys]
    else:
        query = item.question
        haystack = [query.term, query.question_text, query.quote]
    return any(needle in field.lower() for field in haystack)


def _readable_collections(
    spec: SpecStar, actor: Actor, superusers: frozenset[str]
) -> dict[str, _CollCtx]:
    """The collections the ``actor`` may ``read_content`` (see card bodies /
    question text), each with its name and whether the actor may ``add_content``
    (act on its items). Lists all collections unscoped then authorizes each — the
    same idiom as ``readable_collection_ids`` (manual ``list_resources`` is not
    access-scoped)."""
    rm = spec.get_resource_manager(Collection)
    out: dict[str, _CollCtx] = {}
    for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        data = r.data
        assert isinstance(data, Collection)  # the Collection manager only yields Collection
        cid = r.info.resource_id  # ty: ignore[unresolved-attribute]
        created_by = r.info.created_by  # ty: ignore[unresolved-attribute]
        if not authorize(
            actor, "read_content", data.permission, created_by=created_by, superusers=superusers
        ):
            continue
        can_act = authorize(
            actor, "add_content", data.permission, created_by=created_by, superusers=superusers
        )
        out[cid] = _CollCtx(name=data.name, can_act=can_act)
    return out


def build_review_inbox(
    spec: SpecStar,
    *,
    actor: Actor,
    superusers: frozenset[str] = frozenset(),
    resolved: bool = False,
    collection_id: str | None = None,
    kind: str = "all",
    q: str = "",
    actionable: bool = False,
    grouped: bool = False,
    suppressed: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> ReviewInbox:
    """Aggregate every pending-review item (or, with ``resolved``, the history of
    handled ones) the ``actor`` may see, newest first. ``collection_id`` scopes it
    to one collection (the per-collection 待審核 tab reuses this). Items in
    collections the actor can't read are dropped; each surviving item carries
    ``can_act`` so the FE renders read-only rows where the actor lacks write.

    ``kind`` (``"all"`` | ``"cards"`` | ``"questions"``) narrows the page to one
    stream so the FE need not fetch the other. ``q`` is a case-insensitive substring
    over each row's text (card title/body/keys, question term/text/quote), applied
    to the *whole* set so a match anywhere still surfaces. ``limit``/``offset`` page
    the *unified* newest-first stream (cards + questions merged), so the FE renders
    one page instead of thousands of rows; ``total`` on the result still reports the
    full filtered count. ``limit=None`` returns the whole (offset-onward) stream —
    the pre-pagination behaviour."""
    readable = _readable_collections(spec, actor, superusers)
    if collection_id is not None:
        readable = {cid: ctx for cid, ctx in readable.items() if cid == collection_id}

    # #506 P7: the suppressed-audit view is a different stream (dropped candidates
    # live only as suppressed ClusterMembers, not on any run) — return it and stop.
    if suppressed:
        items = suppressed_members(spec, readable)
        if q:
            needle = q.lower()
            items = [s for s in items if needle in s.label.lower()]
        total = len(items)
        page_s = items[offset:] if limit is None else items[offset : offset + limit]
        return ReviewInbox(cards=[], questions=[], suppressed=page_s, total=total)

    merged: list[ReviewCardItem | ReviewQuestionItem] = []
    if kind != "questions":
        merged.extend(_card_items(spec, readable, resolved, collection_id=collection_id))
    if kind != "cards":
        merged.extend(_question_items(spec, readable, resolved))
    if q:
        needle = q.lower()
        merged = [i for i in merged if _row_matches(i, needle)]
    # ``total_actionable`` counts what the actor may write over the whole filtered
    # set (the nav badge reads it from an empty page); computed BEFORE the
    # ``actionable`` filter narrows the rows.
    total_actionable = sum(1 for i in merged if i.can_act)
    # #506 P7: the clustered view groups items by concept FIRST, then pages the
    # clusters ("一群一列"). Grouping must precede pagination (a cluster's members
    # can't straddle a page), and the actionable filter applies at CLUSTER level (a
    # cluster is actionable if any member is), so it runs after grouping.
    if grouped:
        cluster_of = cluster_key_map(spec, list(readable))
        clusters = group_by_cluster(merged, cluster_of)
        if actionable:
            clusters = [c for c in clusters if c.can_act]
        total = len(clusters)
        page_c = clusters[offset:] if limit is None else clusters[offset : offset + limit]
        return ReviewInbox(
            cards=[],
            questions=[],
            clusters=page_c,
            total=total,
            total_actionable=total_actionable,
        )
    if actionable:
        merged = [i for i in merged if i.can_act]
    merged.sort(key=lambda i: i.created_time, reverse=True)  # newest first, across both streams
    total = len(merged)
    page = merged[offset:] if limit is None else merged[offset : offset + limit]
    return ReviewInbox(
        cards=[i for i in page if isinstance(i, ReviewCardItem)],
        questions=[i for i in page if isinstance(i, ReviewQuestionItem)],
        total=total,
        total_actionable=total_actionable,
    )


def _card_items(
    spec: SpecStar,
    readable: dict[str, _CollCtx],
    resolved: bool,
    collection_id: str | None = None,
) -> list[ReviewCardItem]:
    store = CardGenRunStore(spec)
    statuses = _RESOLVED_RUN_STATUSES if resolved else _PENDING_RUN_STATUSES
    items: list[ReviewCardItem] = []
    # #506: when the inbox is scoped to one collection, push that into the indexed
    # query so we read only its runs instead of scanning every collection's.
    for run_id, created, run in store.runs_by_status(statuses, collection_id=collection_id):
        ctx = readable.get(run.collection_id)
        if ctx is None:
            continue  # collection not readable by this user
        for card in ensure_proposal_ids(run.proposals):
            # pending view keeps ACTIVE cards; history keeps the terminal ones.
            if is_active(card) == resolved:
                continue
            items.append(
                ReviewCardItem(
                    run_id=run_id,
                    collection_id=run.collection_id,
                    collection_name=ctx.name,
                    can_act=ctx.can_act,
                    created_time=created,
                    card=card,
                )
            )
    return items


def _question_items(
    spec: SpecStar, readable: dict[str, _CollCtx], resolved: bool
) -> list[ReviewQuestionItem]:
    statuses = _RESOLVED_QUESTION_STATUSES if resolved else _OPEN_QUESTION_STATUSES
    items: list[ReviewQuestionItem] = []
    for qid, created, q in questions_by_status(spec, list(readable), statuses):
        ctx = readable[q.collection_id]
        items.append(
            ReviewQuestionItem(
                qid=qid,
                collection_id=q.collection_id,
                collection_name=ctx.name,
                can_act=ctx.can_act,
                created_time=created,
                question=q,
            )
        )
    return items
