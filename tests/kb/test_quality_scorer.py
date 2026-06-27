"""Issue #105: the QualityScorer judges a document's quality as a knowledge
source against the collection's user-authored rubric — a chunk-based windowed
map-reduce (read each chunk packed into model-sized windows, then synthesise one
holistic doc-level result). All LLM calls stream; a judge failure / unparseable
output yields ``None`` (un-scored, neutral) rather than raising."""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.kb.llm import ILlm
from workspace_app.kb.quality import QualityAssessment, QualityScorer


class _ScriptedLlm(ILlm):
    """Returns queued full responses, one per ``stream()`` call (FIFO), each as a
    single non-reasoning chunk. Records the prompts so a test can assert how many
    LLM calls happened and what they saw."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield (self._responses.pop(0) if self._responses else "", False)


def test_scores_a_doc_from_its_chunks():
    llm = _ScriptedLlm(
        [
            "This section is clear with a little trailing boilerplate.",  # map (window 0)
            '{"score": 72, "breakdown": {"clarity": 0.8, "noise": 0.4}, '
            '"rationale": "Clear but a template footer drags it down."}',  # reduce
        ]
    )
    a = QualityScorer(llm).score(
        rubric="Judge clarity and noise as a knowledge source.",
        chunks=["a short chunk of document text"],
    )
    assert isinstance(a, QualityAssessment)
    assert a.score == 72
    assert a.breakdown == {"clarity": 0.8, "noise": 0.4}
    assert "template footer" in a.rationale


def test_no_rubric_skips_scoring():
    llm = _ScriptedLlm(['{"score": 50, "breakdown": {}, "rationale": "x"}'])
    assert QualityScorer(llm).score(rubric="   ", chunks=["text"]) is None
    assert llm.prompts == []  # no LLM call at all


def test_no_chunks_skips_scoring():
    llm = _ScriptedLlm(['{"score": 50, "breakdown": {}, "rationale": "x"}'])
    assert QualityScorer(llm).score(rubric="Judge it.", chunks=[]) is None
    assert llm.prompts == []


def test_reads_every_chunk_across_windows_then_synthesises():
    # window_chars small ⇒ three chunks pack into two windows; the map runs once
    # per window (reading all content) and the reduce runs once over the notes.
    llm = _ScriptedLlm(
        [
            "note A",  # map window 0 (chunk1 + chunk2)
            "note B",  # map window 1 (chunk3)
            '{"score": 40, "breakdown": {"depth": 0.3}, "rationale": "thin"}',  # reduce
        ]
    )
    a = QualityScorer(llm, window_chars=12).score(
        rubric="Judge depth.", chunks=["chunk1", "chunk2", "chunk3xxxxxx"]
    )
    assert a is not None and a.score == 40
    assert len(llm.prompts) == 3  # 2 map + 1 reduce
    # the reduce prompt synthesises BOTH section notes
    assert "note A" in llm.prompts[-1] and "note B" in llm.prompts[-1]


def test_score_is_clamped_to_0_100():
    for raw_score, want in [("150", 100), ("-7", 0), ("83.6", 84)]:
        reduce = f'{{"score": {raw_score}, "breakdown": {{}}, "rationale": ""}}'
        a = QualityScorer(_ScriptedLlm(["note", reduce])).score(rubric="r", chunks=["c"])
        assert a is not None and a.score == want


def test_unparseable_reduce_yields_unscored():
    llm = _ScriptedLlm(["note", "I think this document is pretty good overall, no JSON here."])
    assert QualityScorer(llm).score(rubric="r", chunks=["c"]) is None


def test_unterminated_json_yields_unscored():
    # A `{` that never closes (a truncated small-model reply) → un-scored, not a crash.
    llm = _ScriptedLlm(["note", 'here it is: {"score": 5'])
    assert QualityScorer(llm).score(rubric="r", chunks=["c"]) is None


def test_missing_or_nonnumeric_score_yields_unscored():
    # Valid JSON, but no usable numeric score ⇒ un-scored.
    for reduce in ['{"breakdown": {}, "rationale": "x"}', '{"score": "high", "rationale": "x"}']:
        assert QualityScorer(_ScriptedLlm(["note", reduce])).score(rubric="r", chunks=["c"]) is None


def test_tolerates_missing_breakdown_and_nonstring_rationale():
    # No breakdown key + a non-string rationale ⇒ empty breakdown, empty rationale.
    llm = _ScriptedLlm(["note", '{"score": 55, "rationale": 123}'])
    a = QualityScorer(llm).score(rubric="r", chunks=["c"])
    assert a is not None and a.score == 55 and a.breakdown == {} and a.rationale == ""


def test_breakdown_keeps_only_numeric_values():
    llm = _ScriptedLlm(
        [
            "note",
            '{"score": 60, "breakdown": {"clarity": 0.9, "label": "good", "noise": 0.2}, '
            '"rationale": "ok"}',
        ]
    )
    a = QualityScorer(llm).score(rubric="r", chunks=["c"])
    assert a is not None and a.breakdown == {"clarity": 0.9, "noise": 0.2}


def test_streams_every_judge_call():
    # feedback_always_stream_llm: the judge surfaces live chunks via on_chunk.
    llm = _ScriptedLlm(["note", '{"score": 70, "breakdown": {}, "rationale": "ok"}'])
    seen: list[str] = []
    QualityScorer(llm).score(rubric="r", chunks=["c"], on_chunk=lambda t, r: seen.append(t))
    assert seen == ["note", '{"score": 70, "breakdown": {}, "rationale": "ok"}']
