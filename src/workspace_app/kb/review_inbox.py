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

from typing import TYPE_CHECKING

import msgspec
from specstar import QB

from ..perm import Actor, authorize
from ..resources.kb import Collection, DocQuestion
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


class ReviewInbox(msgspec.Struct):
    """The aggregated inbox: card proposals + questions, each newest-first."""

    cards: list[ReviewCardItem]
    questions: list[ReviewQuestionItem]


class _CollCtx(msgspec.Struct):
    name: str
    can_act: bool


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
) -> ReviewInbox:
    """Aggregate every pending-review item (or, with ``resolved``, the history of
    handled ones) the ``actor`` may see, newest first. ``collection_id`` scopes it
    to one collection (the per-collection 待審核 tab reuses this). Items in
    collections the actor can't read are dropped; each surviving item carries
    ``can_act`` so the FE renders read-only rows where the actor lacks write."""
    readable = _readable_collections(spec, actor, superusers)
    if collection_id is not None:
        readable = {cid: ctx for cid, ctx in readable.items() if cid == collection_id}

    cards = _card_items(spec, readable, resolved)
    questions = _question_items(spec, readable, resolved)
    cards.sort(key=lambda i: i.created_time, reverse=True)
    questions.sort(key=lambda i: i.created_time, reverse=True)
    return ReviewInbox(cards=cards, questions=questions)


def _card_items(
    spec: SpecStar, readable: dict[str, _CollCtx], resolved: bool
) -> list[ReviewCardItem]:
    store = CardGenRunStore(spec)
    statuses = _RESOLVED_RUN_STATUSES if resolved else _PENDING_RUN_STATUSES
    items: list[ReviewCardItem] = []
    for run_id, created, run in store.runs_by_status(statuses):
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
