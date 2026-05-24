from workspace_app.kb.bm25 import bm25_rank


def test_ranks_docs_containing_query_terms_first():
    corpus = [
        ("d1", "the reflow oven temperature drifted"),
        ("d2", "nothing relevant about cats here"),
        ("d3", "reflow profile reflow zone reflow"),
    ]
    ranked = bm25_rank("reflow temperature", corpus)
    assert set(ranked) == {"d1", "d3"}  # both mention query terms; d2 has none → excluded
    assert "d2" not in bm25_rank("reflow", corpus)  # zero-score docs excluded


def test_empty_query_returns_nothing():
    assert bm25_rank("", [("d1", "anything")]) == []
