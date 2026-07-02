"""Doc-question inbox routes (#377) — the HTTP surface for the global "待釐清"
inbox. Thin adapters over the answer-landing domain (``kb.doc_questions``):

  - ``GET  /kb/doc-questions`` — every open question (the inbox rows).
  - ``POST /kb/doc-questions/{qid}/answer`` — land the human's answer: a term
    question becomes a context card, a description question a clarification-page
    section; returns the produced ``result_ref``.
  - ``POST /kb/doc-questions/{qid}/discard`` — drop a misclassified / irrelevant
    question.

Card / page writes are credited to the request user (the spec's ``default_user``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from ..kb.doc_questions import (
    discard_question,
    land_description_answer,
    land_term_answer,
    list_open_questions,
)
from ..resources.kb import DocQuestion

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..kb.answer_formatter import AnswerCardFormatter
    from ..kb.wiki.store import WikiFileStore


class DocQuestionIO(BaseModel):
    """One inbox row. Term questions carry ``term`` + ``source_doc_ids`` (deduped
    across docs); description questions carry ``source_doc_id`` + ``quote``."""

    id: str
    collection_id: str
    kind: str
    status: str
    question_text: str
    term: str = ""
    source_doc_ids: list[str] = []
    source_doc_id: str = ""
    quote: str = ""


class AnswerBody(BaseModel):
    answer: str


class AnswerOut(BaseModel):
    result_ref: str  # the produced card id or clarification page path


def _to_io(qid: str, q: DocQuestion) -> DocQuestionIO:
    return DocQuestionIO(
        id=qid,
        collection_id=q.collection_id,
        kind=q.kind,
        status=q.status,
        question_text=q.question_text,
        term=q.term,
        source_doc_ids=list(q.source_doc_ids),
        source_doc_id=q.source_doc_id,
        quote=q.quote,
    )


def register_doc_question_routes(
    app: FastAPI | APIRouter,
    spec: SpecStar,
    *,
    formatter: AnswerCardFormatter,
    wiki_store: WikiFileStore,
) -> None:
    def _get(qid: str) -> DocQuestion:
        q = spec.get_resource_manager(DocQuestion).get(qid).data
        assert isinstance(q, DocQuestion)  # narrow Struct|Unset for ty
        return q

    @app.get("/kb/doc-questions")
    def list_doc_questions() -> list[DocQuestionIO]:
        return [_to_io(qid, q) for qid, q in list_open_questions(spec)]

    @app.post("/kb/doc-questions/{qid}/answer")
    async def answer_doc_question(qid: str, body: AnswerBody) -> AnswerOut:
        q = _get(qid)
        if q.kind == "term":
            ref = land_term_answer(spec, qid, answer=body.answer, formatter=formatter)
        else:
            ref = await land_description_answer(
                spec, qid, answer=body.answer, wiki_store=wiki_store
            )
        return AnswerOut(result_ref=ref)

    @app.post("/kb/doc-questions/{qid}/discard")
    def discard_doc_question(qid: str) -> DocQuestionIO:
        discard_question(spec, qid)
        return _to_io(qid, _get(qid))
