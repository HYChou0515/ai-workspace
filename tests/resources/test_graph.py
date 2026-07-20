from specstar import SpecStar

from workspace_app.resources.graph import GraphClaim


def test_make_spec_registers_graph_claim(spec_instance: SpecStar):
    assert spec_instance.get_resource_manager(GraphClaim) is not None


def test_graph_claim_round_trip(spec_instance: SpecStar):
    rm = spec_instance.get_resource_manager(GraphClaim)
    rev = rm.create(
        GraphClaim(
            collection_id="c1",
            source_doc_id="deck-A",
            norm_metric="營收",
            metric="營收",
            value="1.2M",
            period="FY24 Q3",
            unit="USD",
            chunk_id="deck-A#7",
        )
    )
    got = rm.get(rev.resource_id).data
    assert isinstance(got, GraphClaim)
    assert got.collection_id == "c1"
    assert got.norm_metric == "營收"
    assert got.value == "1.2M"
    assert got.period == "FY24 Q3"
    assert got.confidence == 1.0
