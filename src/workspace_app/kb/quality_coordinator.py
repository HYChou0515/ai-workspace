"""Issue #105 — persist a document's quality verdict at index time.

``QualityCoordinator`` is the specstar-coupled half of doc scoring (the pure
judging lives in ``quality.QualityScorer``). It runs from the index pipeline's
"doc is ready" hook (``IndexCoordinator._quality_hook``), so it mirrors the wiki
hook's contract:

- **After ``status="ready"``** — the doc is already usable / searchable; the score
  lands a moment later (un-scored = neutral until then).
- **Only when the collection has a ``quality_rubric``** — otherwise the collection
  is opt-out and the doc stays un-scored.
- **Failure-safe** — a judge error, an unparseable response, or a doc/collection
  that vanished mid-run leaves the doc un-scored; it never raises (the caller also
  wraps it, belt-and-suspenders, so indexing can't be broken by scoring).
"""

from __future__ import annotations

import logging

import msgspec
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..resources.kb import Collection, DocChunk, SourceDoc
from .quality import QualityScorer

logger = logging.getLogger(__name__)


class QualityCoordinator:
    def __init__(self, spec: SpecStar, scorer: QualityScorer) -> None:
        self._spec = spec
        self._scorer = scorer

    def score_doc(self, doc_id: str, acting_user: str) -> None:
        """Judge ``doc_id`` against its collection's rubric and persist the verdict.
        A no-op (doc left un-scored) when the doc/collection is gone, the
        collection has no rubric, the doc has no chunks, or the judge can't produce
        a parseable result. Writes AS ``acting_user`` (the doc's owner) so a worker
        run never erases ``updated_by`` (#83)."""
        doc_rm = self._spec.get_resource_manager(SourceDoc)
        try:
            doc = doc_rm.get(doc_id).data
        except ResourceIDNotFoundError:
            return
        assert isinstance(doc, SourceDoc)
        rubric = self._rubric_for(doc.collection_id)
        if not rubric.strip():
            return  # collection is opt-out (no rubric) → doc stays un-scored
        chunks = self._chunk_texts(doc_id)
        if not chunks:
            return
        assessment = self._scorer.score(rubric=rubric, chunks=chunks)
        if assessment is None:
            return  # judge produced nothing usable → un-scored (neutral)
        with doc_rm.using(user=acting_user):
            doc_rm.update(
                doc_id,
                msgspec.structs.replace(
                    doc,
                    quality_score=assessment.score,
                    quality_breakdown=assessment.breakdown,
                    quality_rationale=assessment.rationale,
                ),
            )

    def _rubric_for(self, collection_id: str) -> str:
        try:
            coll = self._spec.get_resource_manager(Collection).get(collection_id).data
        except ResourceIDNotFoundError:
            return ""
        assert isinstance(coll, Collection)
        return coll.quality_rubric

    def _chunk_texts(self, doc_id: str) -> list[str]:
        """The doc's chunk texts in reading order — the windowed map-reduce input."""
        chrm = self._spec.get_resource_manager(DocChunk)
        rows = [r.data for r in chrm.list_resources((QB["source_doc_id"] == doc_id).build())]
        ordered = sorted((c for c in rows if isinstance(c, DocChunk)), key=lambda c: c.seq)
        return [c.text for c in ordered]
