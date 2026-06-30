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
    depth: int = DEFAULT_DEPTH,
) -> ProbeResult:
    """Rank ``doc_id``'s content for ``question`` (``before``); when ``guidance``
    is given, also rank a non-persisted re-parse of the doc under that candidate
    guidance (``after``). ``guidance is None`` ⇒ no re-parse (``after`` absent);
    ``guidance == ""`` is a real candidate (re-parse with NO steering)."""
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc)
    cid = doc.collection_id
    top_k = retriever.top_k
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
