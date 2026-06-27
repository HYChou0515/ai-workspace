"""Issue #105: the QualityCoordinator scores a doc at index time and persists the
verdict on the SourceDoc. It runs AFTER status="ready" (the doc is already
usable), only when the collection has a rubric, and is failure-safe — a judge
error leaves the doc un-scored (neutral), never raises."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from specstar.types import Binary

from workspace_app.kb.llm import ILlm
from workspace_app.kb.quality import QualityScorer
from workspace_app.kb.quality_coordinator import QualityCoordinator
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk, SourceDoc


class _ScriptedLlm(ILlm):
    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._responses.pop(0) if self._responses else "", False)


def _seed(spec, *, rubric: str, chunks: list[str]):
    """A collection (maybe with a rubric) + a ready doc + its chunks. Returns the
    doc id."""
    crm = spec.get_resource_manager(Collection)
    coll = crm.create(Collection(name="C", quality_rubric=rubric)).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    doc_id = drm.create(
        SourceDoc(collection_id=coll, path="a.md", content=Binary(data=b"x"), status="ready")
    ).resource_id
    chrm = spec.get_resource_manager(DocChunk)
    for i, text in enumerate(chunks):
        chrm.create(
            DocChunk(
                collection_id=coll,
                source_doc_id=doc_id,
                seq=i,
                start=0,
                end=1,
                text=text,
                embedding=[0.0] * EMBED_DIM,
            )
        )
    return doc_id


def _doc(spec, doc_id) -> SourceDoc:
    d = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(d, SourceDoc)
    return d


def test_scores_a_ready_doc_with_a_rubric(spec):
    doc_id = _seed(spec, rubric="Judge clarity.", chunks=["chunk one", "chunk two"])
    scorer = QualityScorer(
        _ScriptedLlm(["note", '{"score": 77, "breakdown": {"clarity": 0.9}, "rationale": "clear"}'])
    )
    QualityCoordinator(spec, scorer).score_doc(doc_id, "u")
    d = _doc(spec, doc_id)
    assert d.quality_score == 77
    assert d.quality_breakdown == {"clarity": 0.9}
    assert d.quality_rationale == "clear"
    assert d.status == "ready"  # scoring never disturbs the doc's index status


def test_no_rubric_collection_is_not_scored(spec):
    doc_id = _seed(spec, rubric="", chunks=["chunk one"])
    # An LLM that WOULD score — proving the skip is the rubric gate, not the judge.
    scorer = QualityScorer(_ScriptedLlm(["n", '{"score": 99, "breakdown": {}, "rationale": "x"}']))
    QualityCoordinator(spec, scorer).score_doc(doc_id, "u")
    assert _doc(spec, doc_id).quality_score is None  # un-scored = neutral


def test_unparseable_judge_result_leaves_doc_unscored(spec):
    # The judge ran but its final response wasn't parseable JSON → the doc stays
    # un-scored (neutral), not crashed. (An LLM that *raises* is caught one level
    # up, in IndexCoordinator._quality_hook — see test_index_coordinator.)
    doc_id = _seed(spec, rubric="Judge it.", chunks=["chunk one"])
    scorer = QualityScorer(_ScriptedLlm(["note", "not json at all"]))
    QualityCoordinator(spec, scorer).score_doc(doc_id, "u")
    assert _doc(spec, doc_id).quality_score is None


def test_missing_doc_is_a_noop(spec):
    scorer = QualityScorer(_ScriptedLlm([]))
    QualityCoordinator(spec, scorer).score_doc("does-not-exist", "u")  # must not raise


def test_doc_whose_collection_is_gone_is_not_scored(spec):
    # A doc pointing at a collection that doesn't resolve → no rubric → un-scored.
    drm = spec.get_resource_manager(SourceDoc)
    doc_id = drm.create(
        SourceDoc(collection_id="ghost", path="a.md", content=Binary(data=b"x"), status="ready")
    ).resource_id
    chrm = spec.get_resource_manager(DocChunk)
    chrm.create(
        DocChunk(
            collection_id="ghost",
            source_doc_id=doc_id,
            seq=0,
            start=0,
            end=1,
            text="x",
            embedding=[0.0] * EMBED_DIM,
        )
    )
    scorer = QualityScorer(_ScriptedLlm(['{"score": 50, "breakdown": {}, "rationale": "x"}']))
    QualityCoordinator(spec, scorer).score_doc(doc_id, "u")
    assert _doc(spec, doc_id).quality_score is None


def test_doc_with_no_chunks_is_not_scored(spec):
    doc_id = _seed(spec, rubric="Judge it.", chunks=[])
    scorer = QualityScorer(_ScriptedLlm(['{"score": 50, "breakdown": {}, "rationale": "x"}']))
    QualityCoordinator(spec, scorer).score_doc(doc_id, "u")
    assert _doc(spec, doc_id).quality_score is None
