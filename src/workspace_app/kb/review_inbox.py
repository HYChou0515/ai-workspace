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

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import msgspec
from specstar import QB

from ..perm import Actor, authorize
from ..resources.kb import ClusterMember, Collection, DocQuestion
from .card_gen import ProposedCard
from .card_proposal import CardProposalStore
from .cluster_member import (
    ClusterRow,
    count_clusters,
    list_members,
    member_query,
    page_clusters,
)
from .doc_questions import get_question, page_questions_by_status, questions_by_status

# The grouped view's type filter → the member kinds that form a concept (#511 P4).
_GROUPED_KINDS: dict[str, tuple[str, ...]] = {
    "all": ("proposal", "term_question"),
    "cards": ("proposal",),
    "questions": ("term_question",),
}

if TYPE_CHECKING:
    from specstar import SpecStar

# #511 P2: card-side pending-vs-history is decided by the proposal's OWN decision
# (ACTIVE = 待審, TERMINAL = history), read straight from the CardProposal rows — no
# run.status gate any more (committed/dismissed run terminals are gone).
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
    title) + ``reason`` are enough to review it. ``reason`` is kind-dependent: a
    proposal can only ever be ``near-card``, while a term question may also be
    ``wiki`` (already written down → not re-asked). See :mod:`kb.reconcile`."""

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
    ``cluster_key`` — the join the grouped SEARCH scan uses to group items. Only
    ``active`` proposal + term_question members participate: ``card`` members are the
    comparison corpus (not shown) and ``suppressed``/``inactive`` members are hidden
    from the default view.

    Perf (#508): ONE batched indexed query over all the collections (not a round-trip
    each), excluding ``card`` members at the DB via the indexed ``kind`` (not in
    Python), reading every member **without its ``embedding``** — see
    :func:`~workspace_app.kb.cluster_member.list_members`. This is the unbounded read
    the search path can't page (a substring match isn't indexed), so it is exactly
    where loading a vector per member hurt most."""
    if not collection_ids:
        return {}
    rm = spec.get_resource_manager(ClusterMember)
    out: dict[tuple[str, str], str] = {}
    for r in list_members(rm, member_query(collection_ids).build()):
        member = cast(ClusterMember, r.data)  # projected — every field but the vector
        out[(member.run_id, member.ref_id)] = member.cluster_key
    return out


def _suppressed_item(readable: dict[str, _CollCtx], member: ClusterMember) -> SuppressedItem:
    ctx = readable[member.collection_id]
    return SuppressedItem(
        collection_id=member.collection_id,
        collection_name=ctx.name,
        kind=member.kind,
        label=member.label or member.norm_key,
        cluster_key=member.cluster_key,
        reason=member.reason,
    )


def page_suppressed(
    spec: SpecStar,
    readable: dict[str, _CollCtx],
    *,
    q: str = "",
    offset: int = 0,
    limit: int | None = None,
) -> tuple[list[SuppressedItem], int]:
    """One page of the auto-suppressed candidates — the audit view (⑥) — across the
    readable collections, newest first, plus the full filtered ``total`` (#511 P4).
    Reads ``state="suppressed"`` ClusterMembers directly (a dropped candidate is on no
    run; card members can't be suppressed, so ``kind`` is always proposal /
    term_question). Without ``q`` it pages natively at the DB
    (``in_(cids) & state=="suppressed"`` sort + offset + limit + count); with ``q`` (a
    label substring, not DB-indexed) it's a bounded scan filtered in Python.

    Reads every member **without its ``embedding``** (#508) — the audit shows a label /
    reason, never the vector. Unlike the queue reads this keeps NO ``kind`` filter: it
    is an audit, so a suppressed member of any kind must stay visible."""
    cids = list(readable)
    if not cids:
        return [], 0
    rm = spec.get_resource_manager(ClusterMember)
    base = QB["collection_id"].in_(cids) & (QB["state"] == "suppressed")
    ordering = base.sort(QB.created_time().desc(), QB.resource_id().asc())

    def _item(r: Any) -> SuppressedItem:
        member = cast(ClusterMember, r.data)  # projected — every field but the vector
        return _suppressed_item(readable, member)

    if not q:
        total = rm.count_resources(base.build())
        paged = ordering.offset(offset)
        if limit is not None:
            paged = paged.limit(limit)
        return [_item(r) for r in list_members(rm, paged.build())], total
    needle = q.lower()
    matched = [
        item
        for r in list_members(rm, ordering.build())
        if needle in (item := _item(r)).label.lower()
    ]
    total = len(matched)
    page = matched[offset:] if limit is None else matched[offset : offset + limit]
    return page, total


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
    # The collections the actor may WRITE (act on): the `actionable` filter narrows
    # the page to these, and `total_actionable` (the nav badge) always counts over
    # them — an ACL-derived, per-collection flag, so it pushes down as a collection
    # filter in both the flat and grouped native queries.
    act_cids = [cid for cid, ctx in readable.items() if ctx.can_act]

    # #506 P7 / #511 P4: the suppressed-audit view is a different stream (dropped
    # candidates live only as suppressed ClusterMembers, not on any run) — paged
    # natively over that state. Return it and stop.
    if suppressed:
        items, total = page_suppressed(spec, readable, q=q, offset=offset, limit=limit)
        return ReviewInbox(cards=[], questions=[], suppressed=items, total=total)

    # #506 P7 / #511 P4: the clustered view is "one row per concept". The default
    # (no ``q``) view pages NATIVELY over the ClusterMember ``GROUP BY cluster_key``
    # (a real ``ORDER BY latest DESC LIMIT/OFFSET`` at the DB), replacing the
    # load-every-item-then-group "fake pagination" (grouped ~60s). A text ``q`` can't
    # push to the DB (no full-text index over the resolved card/question text), so a
    # ``q``-filtered grouped view stays a bounded scan (load → group → filter).
    if grouped:
        if q:
            return _grouped_scan(
                spec,
                readable,
                resolved=resolved,
                kind=kind,
                q=q,
                actionable=actionable,
                limit=limit,
                offset=offset,
            )
        state = "inactive" if resolved else "active"
        kinds = _GROUPED_KINDS[kind]
        page_cids = act_cids if actionable else list(readable)
        groups, total = page_clusters(
            spec, page_cids, state=state, kinds=kinds, offset=offset, limit=limit
        )
        clusters = [
            cluster for g in groups if (cluster := _assemble_cluster(spec, readable, g)) is not None
        ]
        return ReviewInbox(
            cards=[],
            questions=[],
            clusters=clusters,
            total=total,
            total_actionable=count_clusters(spec, act_cids, state=state, kinds=kinds),
        )

    # ── flat view (#511 P3): each stream pages NATIVELY at the DB, no load-all ──
    # ``actionable`` narrows to collections the actor may write; ``total_actionable``
    # is always the count over those collections (the nav badge), independent of the
    # ``actionable`` toggle. ``q`` (a text substring) can't be DB-indexed, so a
    # ``q``-filtered stream is a bounded scan (still just proposal/question rows).
    page_cids = act_cids if actionable else list(readable)
    q_statuses = _RESOLVED_QUESTION_STATUSES if resolved else _OPEN_QUESTION_STATUSES
    cstore = CardProposalStore(spec)

    def _card_page(cids: list[str], *, offset: int, limit: int | None):
        rows, total = cstore.page_for_review(
            cids, resolved=resolved, q=q, offset=offset, limit=limit
        )
        items = [
            ReviewCardItem(
                run_id=run_id,
                collection_id=cid,
                collection_name=readable[cid].name,
                can_act=readable[cid].can_act,
                created_time=created,
                card=card,
            )
            for cid, run_id, created, card in rows
        ]
        return items, total

    def _question_page(cids: list[str], *, offset: int, limit: int | None):
        rows, total = page_questions_by_status(
            spec, cids, q_statuses, q=q, offset=offset, limit=limit
        )
        items = [
            ReviewQuestionItem(
                qid=qid,
                collection_id=question.collection_id,
                collection_name=readable[question.collection_id].name,
                can_act=readable[question.collection_id].can_act,
                created_time=created,
                question=question,
            )
            for qid, created, question in rows
        ]
        return items, total

    def _actionable_total(page: Callable[..., tuple[list[Any], int]], stream_total: int) -> int:
        # When `actionable` is on, page_cids == act_cids so the stream total already
        # IS the actionable count; else count over act_cids (limit=0 → total only).
        return stream_total if actionable else page(act_cids, offset=0, limit=0)[1]

    if kind == "cards":
        cards, total = _card_page(page_cids, offset=offset, limit=limit)
        return ReviewInbox(
            cards=cards,
            questions=[],
            total=total,
            total_actionable=_actionable_total(_card_page, total),
        )
    if kind == "questions":
        questions, total = _question_page(page_cids, offset=offset, limit=limit)
        return ReviewInbox(
            cards=[],
            questions=questions,
            total=total,
            total_actionable=_actionable_total(_question_page, total),
        )
    # kind == "all": a bounded merge — load the top (offset+limit) of EACH stream
    # (enough for the global page however the two interleave), merge-sort, then slice.
    cap = None if limit is None else offset + limit
    cards, card_total = _card_page(page_cids, offset=0, limit=cap)
    questions, q_total = _question_page(page_cids, offset=0, limit=cap)
    merged = [*cards, *questions]
    merged.sort(key=lambda i: i.created_time, reverse=True)  # newest first, across both streams
    page = merged[offset:] if limit is None else merged[offset : offset + limit]
    return ReviewInbox(
        cards=[i for i in page if isinstance(i, ReviewCardItem)],
        questions=[i for i in page if isinstance(i, ReviewQuestionItem)],
        total=card_total + q_total,
        total_actionable=(
            _actionable_total(_card_page, card_total) + _actionable_total(_question_page, q_total)
        ),
    )


def _assemble_cluster(
    spec: SpecStar, readable: dict[str, _CollCtx], group: ClusterRow
) -> ReviewCluster | None:
    """Turn one native ``page_clusters`` group into a :class:`ReviewCluster` (#511
    P4): resolve each member back to its domain row — a ``proposal`` member (id ==
    CardProposal id) to a :class:`ProposedCard`, a ``term_question`` member
    (``ref_id`` == qid) to a :class:`DocQuestion` — carrying the collection name +
    the actor's per-member ``can_act``. ``created_time`` is the concept's newest
    member (the aggregation's sort key); a member whose source cascaded away is
    dropped; a concept with no resolvable member returns ``None``."""
    cluster_key, latest, members = group
    cstore = CardProposalStore(spec)
    cards: list[ReviewCardItem] = []
    questions: list[ReviewQuestionItem] = []
    coll_id = coll_name = ""
    can_act = False
    for member_id, created, member in members:
        # page_clusters scopes members to the readable collections, so every member's
        # collection is present (the actor can read it).
        ctx = readable[member.collection_id]
        if member.kind == "proposal":
            resolved_card = cstore.get_as_proposed(member_id)
            if resolved_card is None:  # the proposal cascaded away
                continue
            run_id, card = resolved_card
            cards.append(
                ReviewCardItem(
                    run_id=run_id,
                    collection_id=member.collection_id,
                    collection_name=ctx.name,
                    can_act=ctx.can_act,
                    created_time=created,
                    card=card,
                )
            )
        else:  # term_question
            question = get_question(spec, member.ref_id)
            if question is None:  # the question cascaded away
                continue
            questions.append(
                ReviewQuestionItem(
                    qid=member.ref_id,
                    collection_id=member.collection_id,
                    collection_name=ctx.name,
                    can_act=ctx.can_act,
                    created_time=created,
                    question=question,
                )
            )
        if not coll_id:  # the first RESOLVED member fixes the concept's collection
            coll_id, coll_name = member.collection_id, ctx.name
        can_act = can_act or ctx.can_act
    if not coll_id:  # every member cascaded away → drop the empty concept
        return None
    return ReviewCluster(
        cluster_key=cluster_key,
        collection_id=coll_id,
        collection_name=coll_name,
        can_act=can_act,
        created_time=latest,
        cards=cards,
        questions=questions,
        size=len(cards) + len(questions),
    )


def _grouped_scan(
    spec: SpecStar,
    readable: dict[str, _CollCtx],
    *,
    resolved: bool,
    kind: str,
    q: str,
    actionable: bool,
    limit: int | None,
    offset: int,
) -> ReviewInbox:
    """The grouped view's ``q``-filtered fallback: a text substring can't push to the
    DB, so load the (filtered) items, group them by ``cluster_key`` in Python, then
    page the concepts. Bounded by the ``q`` match set — far lighter than the old
    unconditional load-all, and only this rare filtered path pays it (the default
    view pages natively — see :func:`build_review_inbox`)."""
    merged: list[ReviewCardItem | ReviewQuestionItem] = []
    if kind != "questions":
        merged.extend(_card_items(spec, readable, resolved))
    if kind != "cards":
        merged.extend(_question_items(spec, readable, resolved))
    needle = q.lower()
    merged = [i for i in merged if _row_matches(i, needle)]
    total_actionable = sum(1 for i in merged if i.can_act)
    cluster_of = cluster_key_map(spec, list(readable))
    clusters = group_by_cluster(merged, cluster_of)
    if actionable:  # a cluster is actionable if any member is
        clusters = [c for c in clusters if c.can_act]
    total = len(clusters)
    page_c = clusters[offset:] if limit is None else clusters[offset : offset + limit]
    return ReviewInbox(
        cards=[], questions=[], clusters=page_c, total=total, total_actionable=total_actionable
    )


def _card_items(
    spec: SpecStar,
    readable: dict[str, _CollCtx],
    resolved: bool,
) -> list[ReviewCardItem]:
    """The card-proposal rows for the inbox — ACTIVE (待審) or, with ``resolved``,
    TERMINAL (history) — read straight from the :class:`CardProposal` rows per
    readable collection (#511 P2). ``readable`` is already scoped to the requested
    collection (build_review_inbox narrows it), so each collection's indexed query
    reads only its own proposals."""
    store = CardProposalStore(spec)
    items: list[ReviewCardItem] = []
    for cid, ctx in readable.items():
        for run_id, created, card in store.list_for_review(cid, resolved=resolved):
            items.append(
                ReviewCardItem(
                    run_id=run_id,
                    collection_id=cid,
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
