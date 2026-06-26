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


def _to_io(p: ProposedCard) -> ProposedCardIO:
    return ProposedCardIO(**msgspec.to_builtins(p))


def _from_io(p: ProposedCardIO) -> ProposedCard:
    return msgspec.convert(p.model_dump(), ProposedCard)


def register_card_gen_routes(app: FastAPI | APIRouter, coordinator: CardGenCoordinator) -> None:
    @app.post("/kb/collections/{collection_id}/context-cards/generate")
    def generate_context_cards(collection_id: str, body: GenerateBody) -> GenerateOut:
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
