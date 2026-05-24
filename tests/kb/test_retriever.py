from specstar import SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import Collection


def _ingest(spec, chunker, embedder, name, text):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename=name, data=text.encode()
    )
    return cid


def test_hybrid_search_surfaces_the_keyword_matching_document(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # one collection with two docs; the query terms only match doc A
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid, user="u", filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ing.ingest(
        collection_id=cid, user="u", filename="cats.md",
        data=b"the cat sat quietly on the warm mat all afternoon",
    )

    passages = Retriever(spec, embedder=embedder).search("reflow temperature", [cid])
    assert passages, "expected at least one passage"
    assert passages[0].document_id == f"{cid}/u/reflow.md"  # keyword-matching doc on top
    assert "reflow" in passages[0].text


def test_search_over_empty_collection_returns_nothing(spec: SpecStar, embedder: HashEmbedder):
    cid = spec.get_resource_manager(Collection).create(Collection(name="empty")).resource_id
    assert Retriever(spec, embedder=embedder).search("anything", [cid]) == []
