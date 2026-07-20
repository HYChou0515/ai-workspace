from specstar import QB, SpecStar

from workspace_app.resources.graph import GraphClaim
from workspace_app.resources.kb import Collection


def test_use_graph_defaults_off_and_the_opted_in_set_is_queryable(spec_instance: SpecStar):
    rm = spec_instance.get_resource_manager(Collection)
    rm.create(Collection(name="reports", use_graph=True))
    rm.create(Collection(name="photos"))  # default off
    on = []
    for r in rm.list_resources(QB["use_graph"].eq(True).build()):
        assert isinstance(r.data, Collection)  # narrow Struct | UnsetType for ty
        on.append(r.data.name)
    assert on == ["reports"]


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
