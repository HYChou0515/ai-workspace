from specstar import SpecStar

from workspace_app.resources.kb import Collection, DocChunk, KbChat, SourceDoc


def test_make_spec_includes_kb_models(spec_instance: SpecStar):
    """`make_spec` registers the KB resources too — same pattern as the
    core managers, just for the knowledge-base side."""
    for model in (Collection, SourceDoc, DocChunk, KbChat):
        assert spec_instance.get_resource_manager(model) is not None


def test_collection_crud_round_trip(spec_instance: SpecStar):
    rm = spec_instance.get_resource_manager(Collection)
    rev = rm.create(Collection(name="HR policies", description="company HR docs"))
    got = rm.get(rev.resource_id).data
    assert got.name == "HR policies"
    assert got.description == "company HR docs"
