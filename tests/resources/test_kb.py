from specstar import SpecStar

from workspace_app.resources import register_all
from workspace_app.resources.kb import Collection, DocChunk, KbChat, SourceDoc


def test_register_all_includes_kb_models(spec_instance: SpecStar):
    register_all(spec_instance)
    for model in (Collection, SourceDoc, DocChunk, KbChat):
        assert spec_instance.get_resource_manager(model) is not None


def test_collection_crud_round_trip(spec_instance: SpecStar):
    register_all(spec_instance)
    rm = spec_instance.get_resource_manager(Collection)
    rev = rm.create(Collection(name="HR policies", description="company HR docs"))
    got = rm.get(rev.resource_id).data
    assert got.name == "HR policies"
    assert got.description == "company HR docs"
