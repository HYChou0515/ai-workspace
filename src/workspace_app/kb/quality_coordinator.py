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
from typing import TYPE_CHECKING, Any

import msgspec
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..resources.kb import Collection, DocChunk, SourceDoc
from .quality import QualityScorer

if TYPE_CHECKING:
    from specstar.types import IResourceManager

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
            # #104: a dedup alias owns no chunks — it shares a content peer's set.
            # Inherit that peer's already-computed verdict instead of leaving the
            # alias neutral while the canonical shows a score (and without re-running
            # the judge on identical text). No scored peer yet → stay neutral.
            peer = self._scored_content_peer(doc, doc_id)
            if peer is not None:
                self._persist(
                    doc_rm,
                    doc_id,
                    doc,
                    acting_user,
                    peer.quality_score,
                    peer.quality_breakdown,
                    peer.quality_rationale,
                )
            return
        assessment = self._scorer.score(rubric=rubric, chunks=chunks)
        if assessment is None:
            return  # judge produced nothing usable → un-scored (neutral)
        self._persist(
            doc_rm,
            doc_id,
            doc,
            acting_user,
            assessment.score,
            assessment.breakdown,
            assessment.rationale,
        )

    def _persist(
        self,
        doc_rm: IResourceManager[SourceDoc],
        doc_id: str,
        doc: SourceDoc,
        acting_user: str,
        score: int | None,
        breakdown: dict[str, Any],
        rationale: str,
    ) -> None:
        with doc_rm.using(user=acting_user):
            doc_rm.update(
                doc_id,
                msgspec.structs.replace(
                    doc,
                    quality_score=score,
                    quality_breakdown=breakdown,
                    quality_rationale=rationale,
                ),
            )

    def _scored_content_peer(self, doc: SourceDoc, doc_id: str) -> SourceDoc | None:
        """A LIVE SourceDoc other than ``doc_id`` in the same collection sharing this
        doc's ``content.file_id`` and already carrying a ``quality_score`` — the
        verdict a chunk-less dedup alias inherits (#104). ``None`` when the content
        has no key yet, or no peer has been scored."""
        fid = doc.content.file_id
        if not isinstance(fid, str) or not fid:
            return None
        drm = self._spec.get_resource_manager(SourceDoc)
        for r in drm.list_resources(
            ((QB["collection_id"] == doc.collection_id) & (QB["file_id"] == fid)).build()
        ):
            if r.info.resource_id == doc_id:  # ty: ignore[unresolved-attribute]
                continue
            peer = r.data
            assert isinstance(peer, SourceDoc)
            if peer.quality_score is not None:
                return peer
        return None

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
