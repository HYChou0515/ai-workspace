from collections.abc import Iterator

import pytest
from specstar import QB

from workspace_app.kb.eval.coordinator import EvalCoordinator
from workspace_app.kb.eval.jobs import EvalJob, EvalJobPayload
from workspace_app.kb.llm import ILlm
from workspace_app.resources import DocChunk, EvalResult, make_spec
from workspace_app.resources.eval import eval_run_id
from workspace_app.resources.kb import Collection, RetrievedPassage


class _FakeLlm(ILlm):
    """Stateless (thread-safe under parallel batch jobs): the answerability check
    always says yes, everything else returns a question."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield ("yes" if "just 'yes' or 'no'" in prompt else "What is this?"), False


class _FakeRetriever:
    """Returns a fixed ranked list so each chunk finds itself at a known rank."""

    def __init__(self, ranked: list[RetrievedPassage]) -> None:
        self._ranked = ranked

    def search(self, query: str, collection_ids: list[str], *, depth: int | None = None):
        return self._ranked


def _mk_collection_with_chunks(spec, name: str, n: int) -> tuple[str, list[str]]:
    coll_id = spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id
    crm = spec.get_resource_manager(DocChunk)
    ids: list[str] = []
    for i in range(n):
        rev = crm.create(
            DocChunk(
                collection_id=coll_id,
                seq=i,
                start=0,
                end=1,
                text=f"chunk {i} of {name}",
                source_doc_id=f"{name}-doc{i}",
            )
        )
        ids.append(rev.resource_id)
    return coll_id, ids


def _p(chunk_id: str, doc: str) -> RetrievedPassage:
    return RetrievedPassage(
        collection_id="c",
        document_id=doc,
        filename=doc,
        start=0,
        end=1,
        source_chunk_ids=[chunk_id],
        text="",
        score=0.0,
    )


async def test_eval_fan_out_writes_a_per_collection_result():
    spec = make_spec()
    coll_id, chunk_ids = _mk_collection_with_chunks(spec, "kb", 3)
    # source at ranks 1, 2, 3 → recall@1 = 1/3, recall@3 = 1.0
    ranked = [_p(cid, f"kb-doc{i}") for i, cid in enumerate(chunk_ids)]
    coord = EvalCoordinator(
        spec,
        _FakeLlm(),
        retriever=_FakeRetriever(ranked),  # ty: ignore[invalid-argument-type]
        sample_size=10,
        batch_size=2,
        ks=(1, 3),
    )

    coord.enqueue_dispatch("run1", seed="s")
    coord.start_consuming()
    await coord.aclose()

    res = spec.get_resource_manager(EvalResult).get(eval_run_id(coll_id, "run1")).data
    assert isinstance(res, EvalResult)
    assert res.collection_id == coll_id
    assert res.n_kept == 3
    assert res.n_dropped == 0
    assert res.recall_chunk["3"] == 1.0
    assert res.recall_chunk["1"] == pytest.approx(1 / 3)
    assert res.mrr_chunk == pytest.approx((1.0 + 1 / 2 + 1 / 3) / 3)


def test_dispatch_enqueues_one_split_per_collection():
    spec = make_spec()
    _mk_collection_with_chunks(spec, "a", 1)
    _mk_collection_with_chunks(spec, "b", 1)
    coord = EvalCoordinator(
        spec,
        _FakeLlm(),
        retriever=_FakeRetriever([]),  # ty: ignore[invalid-argument-type]
    )

    coord._dispatch(EvalJobPayload(kind="dispatch", run_label="r"))

    split_cids: list[str] = []
    for r in spec.get_resource_manager(EvalJob).list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        data = r.data
        assert isinstance(data, EvalJob)
        if data.payload.kind == "split":
            split_cids.append(data.payload.collection_id)
    assert len(split_cids) == 2  # one split per collection
    assert len(set(split_cids)) == 2
