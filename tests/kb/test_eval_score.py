from workspace_app.kb.eval.score import (
    doc_rank,
    mrr,
    passage_rank,
    recall_at_k,
    summarize,
)
from workspace_app.resources.kb import RetrievedPassage


def _p(chunk_ids: list[str], doc: str = "d") -> RetrievedPassage:
    return RetrievedPassage(
        collection_id="c",
        document_id=doc,
        filename=doc,
        start=0,
        end=1,
        source_chunk_ids=chunk_ids,
        text="",
        score=0.0,
    )


def test_passage_rank_is_the_1_based_position_of_the_passage_holding_the_chunk():
    ranked = [_p(["c9"]), _p(["c5", "c2"]), _p(["c1"])]
    assert passage_rank("c2", ranked) == 2


def test_recall_at_k_is_the_fraction_of_items_whose_source_ranked_within_k():
    ranks = [1, 2, 5, None]  # per-item rank of the source; None = never retrieved
    assert recall_at_k(ranks, 1) == 0.25
    assert recall_at_k(ranks, 3) == 0.5
    assert recall_at_k(ranks, 5) == 0.75


def test_mrr_averages_reciprocal_ranks_counting_a_miss_as_zero():
    assert mrr([1, 2, None, None]) == (1.0 + 0.5) / 4


def test_doc_rank_is_the_position_of_the_first_passage_from_that_doc():
    ranked = [_p(["c9"], doc="a"), _p(["c5"], doc="b"), _p(["c1"], doc="a")]
    assert doc_rank("b", ranked) == 2
    assert doc_rank("a", ranked) == 1
    assert doc_rank("z", ranked) is None


def test_summarize_packages_recall_at_each_k_and_mrr():
    m = summarize([1, 2, 5, None], ks=(1, 3, 5))
    assert m.n == 4
    assert m.recall == {1: 0.25, 3: 0.5, 5: 0.75}
    assert m.mrr == (1.0 + 0.5 + 0.2) / 4
