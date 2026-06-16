"""The ingest_to_collection capability (manual §8) — use case 2's reliable commit
step: a workspace file lands in an existing KB collection, idempotently."""

import json

import pytest
from specstar import QB, SpecStar

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.resources.kb import EMBED_DIM, Collection, SourceDoc
from workspace_app.workflow.capabilities import CollectionNotFound, ingest_to_collection


def _ingestor(spec: SpecStar) -> Ingestor:
    return Ingestor(spec, chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
                    embedder=HashEmbedder(dim=EMBED_DIM))


def _collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


async def test_ingest_lands_a_ready_doc_and_writes_a_receipt(spec_instance: SpecStar):
    cid = _collection(spec_instance)
    store = MemoryFileStore()
    await store.write("ws", "/digest/a.md", b"# A\nhello world content")

    doc_id = await ingest_to_collection(
        spec_instance, _ingestor(spec_instance), store,
        workspace_id="ws", collection=cid, path="digest/a.md", user="alice",
    )

    assert doc_id == encode_doc_id(cid, "digest/a.md")
    doc = spec_instance.get_resource_manager(SourceDoc).get(doc_id).data
    assert doc.status == "ready"
    # the receipt makes the deterministic node checkpointable on re-run (§9)
    receipt = json.loads(await store.read("ws", "/step_ingest/digest/a.md.done"))
    assert receipt["doc_id"] == doc_id


async def test_ingest_is_idempotent(spec_instance: SpecStar):
    """Re-ingesting the same path upserts via encode_doc_id — no duplicate doc."""
    cid = _collection(spec_instance)
    store = MemoryFileStore()
    await store.write("ws", "/a.md", b"same bytes")
    ing = _ingestor(spec_instance)
    for _ in range(2):
        await ingest_to_collection(
            spec_instance, ing, store, workspace_id="ws", collection=cid, path="a.md", user="u"
        )
    docs = list(
        spec_instance.get_resource_manager(SourceDoc).list_resources(
            (QB["collection_id"] == cid).build()
        )
    )
    assert len(docs) == 1


async def test_unknown_collection_is_rejected(spec_instance: SpecStar):
    store = MemoryFileStore()
    await store.write("ws", "/a.md", b"x")
    with pytest.raises(CollectionNotFound):
        await ingest_to_collection(
            spec_instance, _ingestor(spec_instance), store,
            workspace_id="ws", collection="no-such", path="a.md", user="u",
        )
