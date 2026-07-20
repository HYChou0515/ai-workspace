from collections.abc import Iterator

from specstar import QB

from workspace_app.kb.graph.coordinator import GraphCoordinator
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim
from workspace_app.resources.kb import Collection, DocChunk


class _FakeLlm(ILlm):
    """Stateless (thread-safe under parallel batch jobs): one fixed claim list."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield '[{"metric": "Revenue", "period": "Q3", "value": "1.2M", "unit": "USD"}]', False


def _mk_collection(spec, name: str, *, use_graph: bool, docs: list[tuple[str, str]]) -> str:
    coll_id = (
        spec.get_resource_manager(Collection)
        .create(Collection(name=name, use_graph=use_graph))
        .resource_id
    )
    crm = spec.get_resource_manager(DocChunk)
    for doc_id, text in docs:
        crm.create(
            DocChunk(collection_id=coll_id, source_doc_id=doc_id, seq=0, start=0, end=1, text=text)
        )
    return coll_id


def _claims(spec, collection_id: str) -> list[GraphClaim]:
    grm = spec.get_resource_manager(GraphClaim)
    out: list[GraphClaim] = []
    for r in grm.list_resources((QB["collection_id"] == collection_id).build()):
        assert isinstance(r.data, GraphClaim)
        out.append(r.data)
    return out


async def test_graph_fan_out_extracts_only_opted_in_collections():
    spec = make_spec()
    on = _mk_collection(spec, "reports", use_graph=True, docs=[("deck-A", "Q3 revenue 1.2M")])
    off = _mk_collection(spec, "photos", use_graph=False, docs=[("photo-1", "a cat")])

    coord = GraphCoordinator(spec, _FakeLlm(), batch_size=10)
    coord.enqueue_dispatch()
    coord.start_consuming()
    await coord.aclose()

    on_claims = _claims(spec, on)
    assert len(on_claims) == 1
    assert on_claims[0].metric == "Revenue"
    assert on_claims[0].source_doc_id == "deck-A"
    assert on_claims[0].norm_metric == "revenue"
    assert _claims(spec, off) == []  # use_graph=False collection is skipped
