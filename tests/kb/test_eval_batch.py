from collections.abc import Iterator

from workspace_app.kb.eval.batch import BatchResult, aggregate, score_batch
from workspace_app.kb.llm import ILlm
from workspace_app.resources.kb import RetrievedPassage


class _FakeLlm(ILlm):
    """Replies in queued order (make_question needs two per chunk: the question,
    then the yes/no answerability check)."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield self._replies.pop(0), False


def _p(chunk_ids: list[str], doc: str) -> RetrievedPassage:
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


def test_score_batch_generates_searches_and_records_chunk_and_doc_ranks():
    # c0 -> kept (yes), c1 -> kept (yes), cX -> dropped (no)
    llm = _FakeLlm("Q0", "yes", "Q1", "yes", "QX", "no")
    ranked = [_p(["c0"], "d0"), _p(["zzz"], "dz"), _p(["c1"], "d1")]

    def fake_search(query: str, collection_ids: list[str]) -> list[RetrievedPassage]:
        return ranked

    chunks = [("c0", "d0", "t0"), ("c1", "d1", "t1"), ("cX", "dX", "tX")]
    out = score_batch(llm, fake_search, ["col"], chunks)

    assert out.chunk_ranks == [1, 3]  # c0 at 1, c1 at 3; cX dropped, not a miss
    assert out.doc_ranks == [1, 3]
    assert out.n_kept == 2
    assert out.n_dropped == 1


def test_score_batch_records_a_miss_as_none_not_a_drop():
    llm = _FakeLlm("Q", "yes")
    ranked = [_p(["other"], "dz")]  # source chunk never comes back

    chunks = [("c0", "d0", "t0")]
    out = score_batch(llm, lambda q, cids: ranked, ["col"], chunks)

    assert out.chunk_ranks == [None]  # a kept item that missed
    assert out.n_kept == 1
    assert out.n_dropped == 0


def test_aggregate_concatenates_batches_and_summarizes_both_grains():
    b1 = BatchResult(chunk_ranks=[1, None], doc_ranks=[1, 2], n_kept=2, n_dropped=1)
    b2 = BatchResult(chunk_ranks=[3], doc_ranks=[None], n_kept=1, n_dropped=0)
    agg = aggregate([b1, b2], ks=(1, 3))

    assert agg.n_kept == 3
    assert agg.n_dropped == 1
    # chunk ranks = [1, None, 3] → recall@1 = 1/3, recall@3 = 2/3
    assert agg.recall_chunk == {"1": 1 / 3, "3": 2 / 3}
    assert agg.mrr_chunk == (1.0 + 1 / 3) / 3
    # doc ranks = [1, 2, None] → recall@1 = 1/3, recall@3 = 2/3
    assert agg.recall_doc == {"1": 1 / 3, "3": 2 / 3}
    assert agg.mrr_doc == (1.0 + 0.5) / 3
