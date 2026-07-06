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

from ..kb.review_inbox import ReviewCardItem, ReviewQuestionItem, build_review_inbox
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


class ReviewInboxOut(BaseModel):
    cards: list[ReviewCardOut]
    questions: list[ReviewQuestionOut]


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
    def review_inbox(resolved: bool = False, collection_id: str | None = None) -> ReviewInboxOut:
        """#481: the global review inbox — pending items across every readable
        collection (``resolved=true`` = the handled-item history; ``collection_id``
        scopes it to one collection's 待審核 tab)."""
        inbox = build_review_inbox(
            spec,
            actor=_actor(),
            superusers=superusers,
            resolved=resolved,
            collection_id=collection_id,
        )
        return ReviewInboxOut(
            cards=[_card_out(i) for i in inbox.cards],
            questions=[_question_out(i) for i in inbox.questions],
        )
