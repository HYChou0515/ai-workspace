"""Global 審核 (review) inbox route (#481) — ``GET /kb/review-inbox``.

The one HTTP surface behind the left-nav 審核 page: every pending-review item —
card-gen proposals + clarification questions — across every collection the caller
may read, permission-filtered so a user only sees (and, per ``can_act``, may act
on) what they're allowed to. A thin adapter over ``kb.review_inbox`` — the
aggregation + permission projection lives in the domain; this converts to
pydantic, reusing the card / question IO models the other KB routers already
speak.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from ..kb.review_inbox import (
    ReviewCardItem,
    ReviewCluster,
    ReviewQuestionItem,
    SuppressedItem,
    build_review_inbox,
)
from ..perm import Actor
from ..resources.groups import groups_of
from .card_gen_routes import ProposedCardIO
from .card_gen_routes import _to_io as _card_to_io
from .doc_question_routes import DocQuestionIO
from .doc_question_routes import _to_io as _question_to_io

if TYPE_CHECKING:
    from specstar import SpecStar


class ReviewCardOut(BaseModel):
    """One card-proposal row: its collection + the run it belongs to (the ``run``
    column) + whether the caller may act, plus the full proposal for the drawer."""

    run_id: str
    collection_id: str
    collection_name: str
    can_act: bool
    created_time: float
    card: ProposedCardIO


class ReviewQuestionOut(BaseModel):
    """One clarification-question row: its collection + whether the caller may act,
    plus the full question."""

    collection_id: str
    collection_name: str
    can_act: bool
    created_time: float
    question: DocQuestionIO


class ReviewClusterOut(BaseModel):
    """#506 P7: one concept's row — the proposals + questions grouped under one
    reconcile ``cluster_key``, so a reviewer resolves a duplicate/related set once."""

    cluster_key: str
    collection_id: str
    collection_name: str
    can_act: bool
    created_time: float
    cards: list[ReviewCardOut]
    questions: list[ReviewQuestionOut]
    size: int = 0


class SuppressedItemOut(BaseModel):
    """#506 P7: one auto-dropped candidate for the suppressed-audit view."""

    collection_id: str
    collection_name: str
    kind: str
    label: str
    cluster_key: str
    reason: str
    # #506/#577 follow-up: for a near-card suppression, the existing card's title.
    target_label: str = ""


class ReviewInboxOut(BaseModel):
    cards: list[ReviewCardOut]
    questions: list[ReviewQuestionOut]
    total: int = 0  # full filtered count across both streams (for "showing X of N")
    total_actionable: int = 0  # actionable count over the whole set (the nav badge)
    # #506 P7: populated instead of cards/questions when grouped=true (one per concept)
    clusters: list[ReviewClusterOut] = []
    # #506 P7: the auto-suppressed candidates, only when suppressed=true
    suppressed: list[SuppressedItemOut] = []


def _card_out(item: ReviewCardItem) -> ReviewCardOut:
    return ReviewCardOut(
        run_id=item.run_id,
        collection_id=item.collection_id,
        collection_name=item.collection_name,
        can_act=item.can_act,
        created_time=item.created_time,
        card=_card_to_io(item.card),
    )


def _question_out(item: ReviewQuestionItem) -> ReviewQuestionOut:
    return ReviewQuestionOut(
        collection_id=item.collection_id,
        collection_name=item.collection_name,
        can_act=item.can_act,
        created_time=item.created_time,
        question=_question_to_io(item.qid, item.question),
    )


def _suppressed_out(item: SuppressedItem) -> SuppressedItemOut:
    return SuppressedItemOut(
        collection_id=item.collection_id,
        collection_name=item.collection_name,
        kind=item.kind,
        label=item.label,
        cluster_key=item.cluster_key,
        reason=item.reason,
        target_label=item.target_label,
    )


def _cluster_out(cluster: ReviewCluster) -> ReviewClusterOut:
    return ReviewClusterOut(
        cluster_key=cluster.cluster_key,
        collection_id=cluster.collection_id,
        collection_name=cluster.collection_name,
        can_act=cluster.can_act,
        created_time=cluster.created_time,
        cards=[_card_out(i) for i in cluster.cards],
        questions=[_question_out(i) for i in cluster.questions],
        size=cluster.size,
    )


def register_review_inbox_routes(
    app: FastAPI | APIRouter,
    spec: SpecStar,
    *,
    get_user_id: Callable[[], str],
    superusers: frozenset[str] = frozenset(),
) -> None:
    def _actor() -> Actor:
        me = get_user_id()
        return Actor.human(me, groups=groups_of(spec, me))

    @app.get("/kb/review-inbox")
    def review_inbox(
        resolved: bool = False,
        collection_id: str | None = None,
        kind: str = "all",
        q: str = "",
        actionable: bool = False,
        grouped: bool = False,
        suppressed: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> ReviewInboxOut:
        """#481/#506: the review inbox — pending items across every readable
        collection (``resolved=true`` = the handled-item history; ``collection_id``
        scopes it to one collection's 待審核 tab). Server-side ``kind``/``q``/
        ``actionable`` filters + ``limit``/``offset`` paging keep the FE from loading
        thousands of rows; ``total``/``total_actionable`` report the full counts.
        ``grouped=true`` returns one ``clusters`` row per concept (the reconcile
        ``cluster_key`` — a proposal + a question about the same thing collapse),
        paginated by cluster; the flat ``cards``/``questions`` are then empty."""
        inbox = build_review_inbox(
            spec,
            actor=_actor(),
            superusers=superusers,
            resolved=resolved,
            collection_id=collection_id,
            kind=kind,
            q=q,
            actionable=actionable,
            grouped=grouped,
            suppressed=suppressed,
            limit=limit,
            offset=offset,
        )
        return ReviewInboxOut(
            cards=[_card_out(i) for i in inbox.cards],
            questions=[_question_out(i) for i in inbox.questions],
            total=inbox.total,
            total_actionable=inbox.total_actionable,
            clusters=[_cluster_out(c) for c in inbox.clusters],
            suppressed=[_suppressed_out(s) for s in inbox.suppressed],
        )
