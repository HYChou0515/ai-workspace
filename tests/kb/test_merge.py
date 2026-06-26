from workspace_app.kb.merge import ScoredChunk, merge_passages

DOC_TEXT = {"d1": "alpha beta gamma delta", "d2": "second document body here"}


def _text_of(doc_id: str) -> str:
    return DOC_TEXT[doc_id]


def _c(chunk_id, doc, seq, start, end, score, provenance=None):
    return ScoredChunk(
        chunk_id=chunk_id,
        document_id=doc,
        collection_id="c",
        filename=f"{doc}.md",
        seq=seq,
        start=start,
        end=end,
        score=score,
        provenance=provenance or {},
    )


def test_merges_overlapping_chunks_from_same_doc_into_one_passage():
    chunks = [
        _c("d1#0", "d1", 0, 0, 10, 0.9),  # "alpha beta"
        _c("d1#1", "d1", 1, 6, 22, 0.5),  # "beta gamma delta" — overlaps at "beta"
    ]
    out = merge_passages(chunks, text_of=_text_of)
    assert len(out) == 1
    p = out[0]
    assert p.document_id == "d1"
    assert (p.start, p.end) == (0, 22)
    assert p.text == "alpha beta gamma delta"  # verbatim source[0:22]
    assert set(p.source_chunk_ids) == {"d1#0", "d1#1"}
    assert p.score == 0.9  # max of the merged chunks


def test_merged_passage_aggregates_chunk_provenance_in_seq_order():
    """Issue #254: a passage that merges chunks across a page boundary keeps the
    union of their locations (distinct values, seq order) so the LLM/UI can say
    'p.3–4'. Single repeated section collapses to one entry."""
    chunks = [
        _c("d1#0", "d1", 0, 0, 10, 0.9, {"page": 3, "section": "Ch.2 > 2.1"}),
        _c("d1#1", "d1", 1, 6, 22, 0.5, {"page": 4, "section": "Ch.2 > 2.1"}),
    ]
    out = merge_passages(chunks, text_of=_text_of)
    assert len(out) == 1
    assert out[0].provenance == {"page": [3, 4], "section": ["Ch.2 > 2.1"]}


def test_passage_provenance_is_empty_when_chunks_have_none():
    """Graceful degrade: legacy chunks with no provenance → empty dict."""
    out = merge_passages([_c("d1#0", "d1", 0, 0, 10, 0.9)], text_of=_text_of)
    assert out[0].provenance == {}


def test_separate_docs_and_gaps_stay_separate_passages_ordered_by_score():
    chunks = [
        _c("d1#0", "d1", 0, 0, 5, 0.3),  # gap from the next
        _c("d1#2", "d1", 2, 16, 22, 0.4),
        _c("d2#0", "d2", 0, 0, 6, 0.95),
    ]
    out = merge_passages(chunks, text_of=_text_of)
    assert [p.document_id for p in out] == ["d2", "d1", "d1"]  # by score desc
    assert {(p.start, p.end) for p in out if p.document_id == "d1"} == {(0, 5), (16, 22)}
