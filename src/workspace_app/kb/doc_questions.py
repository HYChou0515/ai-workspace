"""Doc-clarification questions (#377) — the durable state + helpers behind "AI
asks instead of hallucinating". The per-doc digest raises a ``DocQuestion`` when
it can't confidently define a term or follow a passage; a human answers it in the
global inbox and the answer lands directly (trusted).

This module owns the question lifecycle: raising (with collection-level dedup for
term questions), answering, discarding, and the inbox query — all through the
``DocQuestion`` resource, mirroring ``kb.context_cards``' deterministic style.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec
from specstar import QB

from ..resources.kb import DocQuestion
from .card_gen import DescriptionQuestionDraft, TermQuestionDraft
from .context_cards import norm

if TYPE_CHECKING:
    from specstar import SpecStar
    from specstar.types import IResourceManager


def plan_doc_questions(
    term_questions: list[TermQuestionDraft],
    description_questions: list[DescriptionQuestionDraft],
    *,
    carded_norm_keys: set[str],
    cap: int,
) -> tuple[list[TermQuestionDraft], list[DescriptionQuestionDraft]]:
    """Apply the deterministic digest guardrails (#377 Q6) to one doc's raised
    questions, returning the ones actually worth raising:

      - **①** drop term questions whose ``norm(term)`` is already carded (don't
        re-ask a defined term); and
      - **③** cap the doc's total questions at ``cap`` — terms (definitions) fill
        the budget first, then descriptions take what's left.

    (Guardrail ② — "the doc already explains it" — is the LLM's judgment at draft
    time, not a deterministic filter.)"""
    terms = [q for q in term_questions if norm(q.term) not in carded_norm_keys]
    terms = terms[: max(cap, 0)]
    descs = description_questions[: max(cap - len(terms), 0)]
    return terms, descs


def _open_term_question(
    rm: IResourceManager[DocQuestion], collection_id: str, norm_key: str
) -> tuple[str, DocQuestion] | None:
    """The single open term question for ``norm_key`` in ``collection_id`` (with its
    id), or None — the dedup target. Only ``open`` questions merge: an answered term
    already has a card, and a discarded one re-opens fresh (#377 Q11)."""
    q = (
        (QB["collection_id"] == collection_id)
        & (QB["kind"] == "term")
        & (QB["status"] == "open")
        & (QB["norm_key"] == norm_key)
    )
    for r in rm.list_resources(q.build()):
        data = r.data
        assert isinstance(data, DocQuestion)  # narrow Struct|Unset for ty
        return r.info.resource_id, data  # ty: ignore[unresolved-attribute]
    return None


def open_or_merge_term_question(
    spec: SpecStar,
    *,
    collection_id: str,
    term: str,
    source_doc_id: str,
    question_text: str,
) -> str:
    """Raise a term question for ``term`` in ``collection_id`` and return its id.

    Term questions dedupe at collection level by ``norm(term)``: a fresh unknown
    term opens a new question carrying the raising doc; a term already open just
    accumulates the new ``source_doc_id`` (a human answers once, it applies
    everywhere)."""
    rm = spec.get_resource_manager(DocQuestion)
    existing = _open_term_question(rm, collection_id, norm(term))
    if existing is not None:
        qid, data = existing
        if source_doc_id not in data.source_doc_ids:  # idempotent across digest re-runs
            rm.update(
                qid,
                msgspec.structs.replace(data, source_doc_ids=[*data.source_doc_ids, source_doc_id]),
            )
        return qid
    return rm.create(
        DocQuestion(
            collection_id=collection_id,
            kind="term",
            term=term,
            norm_key=norm(term),
            source_doc_ids=[source_doc_id],
            question_text=question_text,
        )
    ).resource_id


def open_questions_for_collections(
    spec: SpecStar, collection_ids: list[str]
) -> list[tuple[str, DocQuestion]]:
    """The global inbox's rows: every ``open`` question across ``collection_ids``,
    paired with its id (the FE targets it to answer / discard). Scoped per collection
    via the indexed ``collection_id`` + ``status`` query — never a full scan."""
    rm = spec.get_resource_manager(DocQuestion)
    out: list[tuple[str, DocQuestion]] = []
    for cid in collection_ids:
        q = (QB["collection_id"] == cid) & (QB["status"] == "open")
        for r in rm.list_resources(q.build()):
            data = r.data
            assert isinstance(data, DocQuestion)  # narrow Struct|Unset for ty
            out.append((r.info.resource_id, data))  # ty: ignore[unresolved-attribute]
    return out


def answer_question(spec: SpecStar, qid: str, *, answer: str, result_ref: str) -> None:
    """Record a human's answer and flip the question to ``answered``. ``result_ref``
    points at what the answer produced (a context-card id or clarification page path),
    for provenance. The answer is trusted and lands directly (#377 Q9)."""
    rm = spec.get_resource_manager(DocQuestion)
    data = rm.get(qid).data
    assert isinstance(data, DocQuestion)  # narrow Struct|Unset for ty
    rm.update(
        qid, msgspec.structs.replace(data, status="answered", answer=answer, result_ref=result_ref)
    )


def discard_question(spec: SpecStar, qid: str) -> None:
    """Flip a question to ``discarded`` — the human's recourse for a misclassified or
    irrelevant question (#377 Q7). Not permanent: the term can re-open if it later
    surfaces in a new source doc (#377 Q11)."""
    rm = spec.get_resource_manager(DocQuestion)
    data = rm.get(qid).data
    assert isinstance(data, DocQuestion)  # narrow Struct|Unset for ty
    rm.update(qid, msgspec.structs.replace(data, status="discarded"))


def add_description_question(
    spec: SpecStar,
    *,
    collection_id: str,
    source_doc_id: str,
    quote: str,
    question_text: str,
) -> str:
    """Raise a description question — a passage in ``source_doc_id`` the digest
    couldn't follow, quoted verbatim. Doc-specific and never deduped (unlike term
    questions); its answer lands on the collection's clarification wiki page."""
    rm = spec.get_resource_manager(DocQuestion)
    return rm.create(
        DocQuestion(
            collection_id=collection_id,
            kind="description",
            source_doc_id=source_doc_id,
            quote=quote,
            question_text=question_text,
        )
    ).resource_id
