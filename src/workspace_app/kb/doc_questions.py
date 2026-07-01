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

from ..resources.kb import ContextCard, DocQuestion
from .card_gen import DescriptionQuestionDraft, TermQuestionDraft
from .context_cards import derive_norm_keys, find_cards_by_key, norm
from .wiki.store import CLARIFICATIONS_PATH

if TYPE_CHECKING:
    from specstar import SpecStar
    from specstar.types import IResourceManager

    from .answer_formatter import AnswerCardFormatter
    from .wiki.store import WikiFileStore


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


def land_term_answer(
    spec: SpecStar, qid: str, *, answer: str, formatter: AnswerCardFormatter
) -> str:
    """Land a human's answer to a TERM question as a context card and mark the
    question answered (#377 Q9/Q13). The answer is trusted — the ``formatter`` only
    tidies it into a ``(title, body)``; the card is keyed by the question's term.
    Create-or-update: an existing card for the term is overwritten in place rather
    than duplicated. Returns the card id (also stored as the question's
    ``result_ref`` for provenance)."""
    rm = spec.get_resource_manager(DocQuestion)
    q = rm.get(qid).data
    assert isinstance(q, DocQuestion)  # narrow Struct|Unset for ty
    title, body = formatter.format(term=q.term, answer=answer)
    card = ContextCard(
        collection_id=q.collection_id,
        keys=[q.term],
        norm_keys=derive_norm_keys([q.term]),
        title=title,
        body=body,
    )
    card_rm = spec.get_resource_manager(ContextCard)
    existing = find_cards_by_key(spec, q.collection_id, q.term)
    if existing:
        card_id = existing[0][0]
        card_rm.create_or_update(card_id, card)  # overwrite in place, not a duplicate
    else:
        card_id = card_rm.create(card).resource_id
    answer_question(spec, qid, answer=answer, result_ref=card_id)
    return card_id


_CLARIFICATIONS_HEADER = (
    "# Clarifications\n\n"
    "Human answers to passages the digest couldn't follow on its own (#377). Each "
    "entry quotes the source passage, then the answer.\n"
)


def _render_clarification(question_text: str, quote: str, answer: str) -> str:
    """One faithful clarification section: the question, the quoted passage as a
    blockquote, then the human's answer verbatim — no AI rewriting (#377 Q10)."""
    parts = ["\n---\n"]
    if question_text:
        parts.append(f"\n**{question_text}**\n")
    if quote:
        parts.append("\n> " + quote.replace("\n", "\n> ") + "\n")
    parts.append(f"\n{answer}\n")
    return "".join(parts)


async def land_description_answer(
    spec: SpecStar, qid: str, *, answer: str, wiki_store: WikiFileStore
) -> str:
    """Land a human's answer to a DESCRIPTION question by appending a faithful
    Q&A section (quote + answer, no AI rewriting) to the collection's reserved
    clarification wiki page, and mark the question answered (#377 Q9/Q10). Returns
    the page path (also stored as ``result_ref``). The page is builder-immune (see
    ``MaintainerWikiStore``), so a wiki rebuild can't clobber it."""
    rm = spec.get_resource_manager(DocQuestion)
    q = rm.get(qid).data
    assert isinstance(q, DocQuestion)  # narrow Struct|Unset for ty
    cid = q.collection_id
    entry = _render_clarification(q.question_text, q.quote, answer)
    prior = (
        await wiki_store.read(cid, CLARIFICATIONS_PATH)
        if await wiki_store.exists(cid, CLARIFICATIONS_PATH)
        else _CLARIFICATIONS_HEADER.encode()
    )
    await wiki_store.write(cid, CLARIFICATIONS_PATH, prior + entry.encode())
    answer_question(spec, qid, answer=answer, result_ref=CLARIFICATIONS_PATH)
    return CLARIFICATIONS_PATH


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
