"""Context-card generation routes (#175) — the HTTP surface for "自動 context
card". The heavy work lives on a specstar job (``CardGenCoordinator``); these are
thin adapters:

  - ``POST /kb/collections/{cid}/context-cards/generate`` — start a run over the
    selected documents, returns the job id to poll.
  - ``GET  /kb/context-card-gen/{job_id}`` — the run's status + current proposals
    (for the review surface; resumable — proposals carry the reviewer's decisions).
  - ``POST /kb/context-card-gen/{job_id}/review`` — persist the reviewer's edited /
    decided proposals back onto the job (so leaving + returning restores progress).
  - ``POST /kb/context-card-gen/{job_id}/commit`` — write the accepted proposals to
    real cards; returns the created / updated / skipped tallies.

Proposals cross the wire as pydantic models (FastAPI's I/O layer); they convert
to/from the job's msgspec ``ProposedCard`` via ``msgspec.convert`` /
``msgspec.to_builtins``.
"""

from __future__ import annotations

import msgspec
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from ..kb.card_gen import ProposedCard
from ..kb.card_gen_coordinator import CardGenCoordinator


class GenerateBody(BaseModel):
    """Start a run over the user-selected documents (#175 Q2 — picked by updated
    time on the FE)."""

    doc_ids: list[str]


class GenerateOut(BaseModel):
    job_id: str


class ProvenanceIO(BaseModel):
    doc_id: str
    path: str
    snippet: str = ""


class ProposedCardIO(BaseModel):
    keys: list[str]
    id: str = ""
    title: str = ""
    body: str = ""
    confident: bool = True
    mode: str = "new"
    target_card_id: str | None = None
    provenance: list[ProvenanceIO] = []
    decision: str = "pending"


class GenStatusOut(BaseModel):
    status: str
    proposals: list[ProposedCardIO]


class ReviewBody(BaseModel):
    proposals: list[ProposedCardIO]


class CommitOut(BaseModel):
    created: int
    updated: int
    skipped: int


class DecideBody(BaseModel):
    """#481 inline accept/reject: flip one proposal's decision by id."""

    card_id: str
    decision: str


class CardRef(BaseModel):
    """#481: one proposal to commit, addressed by its run + stable card id."""

    run_id: str
    card_id: str


class CommitCardsBody(BaseModel):
    """#481: commit an arbitrary set of proposal cards; per-run commit is the
    special case where every ref shares a run."""

    cards: list[CardRef]


class PendingRunOut(BaseModel):
    """One row of a collection's 待審核 queue (#415): a finalized, unreviewed run."""

    run_id: str
    collection_id: str
    proposal_count: int


def _to_io(p: ProposedCard) -> ProposedCardIO:
    return ProposedCardIO(**msgspec.to_builtins(p))


def _from_io(p: ProposedCardIO) -> ProposedCard:
    return msgspec.convert(p.model_dump(), ProposedCard)


def register_card_gen_routes(app: FastAPI | APIRouter, coordinator: CardGenCoordinator) -> None:
    @app.post("/kb/collections/{collection_id}/context-cards/generate")
    def generate_context_cards(collection_id: str, body: GenerateBody) -> GenerateOut:
        # One-shot: draft cards over the picked (ready) docs. Still-indexing docs
        # are skipped; whether they auto-generate once ready is governed by the
        # collection's user-owned `auto_digest` setting, not this action.
        return GenerateOut(job_id=coordinator.enqueue(collection_id, body.doc_ids))

    @app.get("/kb/context-card-gen/{job_id}")
    def context_card_gen_status(job_id: str) -> GenStatusOut:
        art = coordinator.proposals(job_id)
        return GenStatusOut(
            status=coordinator.status(job_id).value,
            proposals=[_to_io(p) for p in art.proposals],
        )

    @app.post("/kb/context-card-gen/{job_id}/review")
    def review_context_card_gen(job_id: str, body: ReviewBody) -> GenStatusOut:
        coordinator.save_review(job_id, [_from_io(p) for p in body.proposals])
        art = coordinator.proposals(job_id)
        return GenStatusOut(
            status=coordinator.status(job_id).value,
            proposals=[_to_io(p) for p in art.proposals],
        )

    @app.post("/kb/context-card-gen/{job_id}/commit")
    def commit_context_card_gen(job_id: str) -> CommitOut:
        r = coordinator.commit(job_id)
        return CommitOut(created=r.created, updated=r.updated, skipped=r.skipped)

    @app.post("/kb/context-card-gen/{job_id}/proposals/{card_id}")
    def update_context_card(job_id: str, card_id: str, body: ProposedCardIO) -> GenStatusOut:
        """#481: persist the reviewer's edited proposal (drawer edit: body/title +
        decision) by id; returns the run's refreshed proposals."""
        coordinator.update_proposal(job_id, card_id, _from_io(body))
        art = coordinator.proposals(job_id)
        return GenStatusOut(
            status=coordinator.status(job_id).value,
            proposals=[_to_io(p) for p in art.proposals],
        )

    @app.post("/kb/context-card-gen/{job_id}/decide")
    def decide_context_card(job_id: str, body: DecideBody) -> GenStatusOut:
        """#481: persist one card's inline accept/reject; returns the run's refreshed
        proposals so the FE stays in sync (a settle may have resolved the run)."""
        coordinator.decide(job_id, body.card_id, body.decision)
        art = coordinator.proposals(job_id)
        return GenStatusOut(
            status=coordinator.status(job_id).value,
            proposals=[_to_io(p) for p in art.proposals],
        )

    @app.post("/kb/context-card-gen/commit")
    def commit_context_cards(body: CommitCardsBody) -> CommitOut:
        """#481: the multi-card (cross-run) commit — write exactly the referenced
        cards and settle each affected run."""
        r = coordinator.commit_cards([(c.run_id, c.card_id) for c in body.cards])
        return CommitOut(created=r.created, updated=r.updated, skipped=r.skipped)

    @app.get("/kb/collections/{collection_id}/context-card-gen")
    def list_pending_card_gen(collection_id: str) -> list[PendingRunOut]:
        """The collection's 待審核 queue — finalized runs awaiting review (#415)."""
        return [
            PendingRunOut(**msgspec.to_builtins(s)) for s in coordinator.pending_runs(collection_id)
        ]

    @app.post("/kb/context-card-gen/{job_id}/dismiss")
    def dismiss_context_card_gen(job_id: str) -> None:
        """Discard a run's proposals — it leaves the queue without writing cards."""
        coordinator.dismiss(job_id)
