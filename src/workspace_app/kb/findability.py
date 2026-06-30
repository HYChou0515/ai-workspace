"""Findability probe (#328) — the read-only core of the prompt-tuning modal.

Given a doc and a representative question, report where THIS doc's content ranks
in the real retriever (deep, beyond the top-k a user sees). With a CANDIDATE
``parser_guidance``, also report the "after": re-parse this one doc under that
guidance (``Ingestor.dry_run_chunks``) and rank it through the retriever
``Overlay`` (the doc's stored chunks shadowed, the rest of the collection held
fixed). Nothing is persisted — the modal only writes when the user hits "Apply".

The metric is deliberately just RANK (no hit@k / MRR / score): the user eyeballs
"did my content move up?" while tuning the extraction prompt. Ranks are reported
at the merged-passage grain (a passage is contiguous chunks of one doc — the unit
retrieval actually returns and the user actually sees).
"""

from __future__ import annotations

from dataclasses import dataclass

from specstar import SpecStar

from ..resources.kb import RetrievedPassage, SourceDoc
from .ingest import Ingestor
from .llm import ILlm, OnChunk
from .provenance import format_location
from .retriever import Overlay, Retriever

# How deep to rank — far past the top_k=5 a user sees, so a buried chunk reads as
# "#37" rather than just "absent". Clamped by the route.
DEFAULT_DEPTH = 50
# Passage text is a preview, not the whole doc — keep the payload lean.
_SNIPPET = 600


@dataclass(frozen=True)
class ProbePassage:
    """One of the target doc's passages in the ranked results."""

    rank: int  # 1-based position in the deep ranked list (across all docs)
    in_top_k: bool  # within the top_k a normal search returns (what the user sees)
    text: str  # passage preview (truncated)
    location: str  # "p.3" / "slide 2 · Ch.2" / "" — human structural locator


@dataclass(frozen=True)
class ProbeSide:
    """Where the target doc lands for the question, one parse-state's worth."""

    passages: list[ProbePassage]  # the doc's passages within `depth`, best rank first
    best_rank: int | None  # min rank, or None if the doc didn't surface at all


@dataclass(frozen=True)
class ProbeResult:
    top_k: int
    depth: int
    before: ProbeSide  # the doc's currently-indexed chunks
    after: ProbeSide | None  # the candidate-guidance re-parse (None if none given)


def _side(passages: list[RetrievedPassage], doc_id: str, top_k: int) -> ProbeSide:
    """Pull the target doc's passages out of the full ranked list, tagging each
    with its rank (= position in the list) and whether it made the top_k cut."""
    out = [
        ProbePassage(
            rank=rank,
            in_top_k=rank <= top_k,
            text=p.text[:_SNIPPET],
            location=format_location(p.provenance),
        )
        for rank, p in enumerate(passages, start=1)
        if p.document_id == doc_id
    ]
    return ProbeSide(passages=out, best_rank=out[0].rank if out else None)


def probe_findability(
    spec: SpecStar,
    retriever: Retriever,
    ingestor: Ingestor,
    *,
    doc_id: str,
    question: str,
    guidance: str | None = None,
    k: int | None = None,
) -> ProbeResult:
    """Rank ``doc_id``'s content for ``question`` (``before``); when ``guidance``
    is given, also rank a non-persisted re-parse of the doc under that candidate
    guidance (``after``). ``guidance is None`` ⇒ no re-parse (``after`` absent);
    ``guidance == ""`` is a real candidate (re-parse with NO steering).

    ``k`` (#356) is the modal's slider — the top-k cutoff that flags which
    passages a user actually sees (``in_top_k``). ``None`` ⇒ the retriever's real
    ``top_k`` (production default). We always rank to ``max(DEFAULT_DEPTH, k)`` so
    a buried passage still reads as "#37" rather than just "absent", even when k
    is small — and so a large k is fully covered."""
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc)
    cid = doc.collection_id
    top_k = k if k is not None else retriever.top_k
    depth = max(DEFAULT_DEPTH, top_k)
    before = _side(retriever.search(question, [cid], depth=depth), doc_id, top_k)
    after: ProbeSide | None = None
    if guidance is not None:
        virtual_chunks, virtual_text = ingestor.dry_run_chunks(doc_id, guidance=guidance)
        passages = retriever.search(
            question,
            [cid],
            depth=depth,
            overlay=Overlay(
                virtual_chunks=virtual_chunks, shadow_doc_id=doc_id, virtual_text=virtual_text
            ),
        )
        after = _side(passages, doc_id, top_k)
    return ProbeResult(top_k=top_k, depth=depth, before=before, after=after)


def doc_passages_in_top_k(
    spec: SpecStar,
    retriever: Retriever,
    ingestor: Ingestor,
    *,
    doc_id: str,
    question: str,
    k: int,
    guidance: str | None = None,
) -> list[RetrievedPassage]:
    """#356: the FULL-text passages of ``doc_id`` that actually make the top-``k``
    of the real ranked list for ``question`` — the exact context window a normal
    search would hand an answerer. e.g. the doc's passages rank 1, 4, 6, 12 and
    ``k=5`` ⇒ only ranks 1 & 4 come back. ``guidance is None`` ⇒ the doc's
    currently-indexed chunks (the "before"); a string ⇒ the candidate re-parse
    (``dry_run_chunks`` → ``Overlay``, the "after"), so the answer can preview the
    effect of a tuned prompt. We rank to ``max(DEFAULT_DEPTH, k)`` so a large k is
    still covered. Returns passages best-rank-first; possibly empty (the doc had
    nothing in the top-k — itself the diagnostic)."""
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc)
    cid = doc.collection_id
    depth = max(DEFAULT_DEPTH, k)
    if guidance is None:
        ranked = retriever.search(question, [cid], depth=depth)
    else:
        virtual_chunks, virtual_text = ingestor.dry_run_chunks(doc_id, guidance=guidance)
        ranked = retriever.search(
            question,
            [cid],
            depth=depth,
            overlay=Overlay(
                virtual_chunks=virtual_chunks, shadow_doc_id=doc_id, virtual_text=virtual_text
            ),
        )
    return [p for rank, p in enumerate(ranked, start=1) if p.document_id == doc_id and rank <= k]


def build_answer_prompt(system_prompt: str, question: str, passages: list[RetrievedPassage]) -> str:
    """Compose the one-shot answer prompt: the kb_chat system prompt, then the
    fixed passages presented as the numbered ``[n]`` retrieval set (so the agent's
    citation rules apply), then the question. An EMPTY passage list is presented
    plainly — the system prompt then handles it the same way it handles a search
    that found nothing (says the KB doesn't cover it / answers from general
    knowledge), so we never fabricate a grounded answer."""
    if passages:
        body = "\n\n".join(f"[{i}] {p.text}" for i, p in enumerate(passages, start=1))
        retrieved = (
            "Below are the ONLY passages retrieved from the knowledge base for this "
            f"question (numbered for citation):\n\n{body}"
        )
    else:
        retrieved = "No passages were retrieved from the knowledge base for this question."
    return f"{system_prompt}\n\n{retrieved}\n\nQuestion: {question}"


def answer_from_passages(
    llm: ILlm,
    *,
    system_prompt: str,
    question: str,
    passages: list[RetrievedPassage],
    on_chunk: OnChunk | None = None,
) -> str:
    """#356 "Try answer": stream a single, tool-less answer to ``question`` from
    ONLY ``passages`` — the kb_chat agent's prompt + model, but with retrieval
    forced to this fixed doc∩top-k set (no self-search, so the controlled k
    experiment holds). Streams every chunk to ``on_chunk`` (live answer) and
    returns the joined non-reasoning text."""
    prompt = build_answer_prompt(system_prompt, question, passages)
    return llm.collect(prompt, on_chunk=on_chunk)
