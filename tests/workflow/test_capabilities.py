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
from workspace_app.workflow.handle import WorkflowHandle


def _ingestor(spec: SpecStar) -> Ingestor:
    return Ingestor(
        spec,
        chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        embedder=HashEmbedder(dim=EMBED_DIM),
    )


def _collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


async def test_ingest_lands_a_ready_doc_and_writes_a_receipt(spec_instance: SpecStar):
    cid = _collection(spec_instance)
    store = MemoryFileStore()
    await store.write("ws", "/digest/a.md", b"# A\nhello world content")

    doc_id = await ingest_to_collection(
        spec_instance,
        _ingestor(spec_instance),
        store,
        workspace_id="ws",
        collection=cid,
        path="digest/a.md",
        user="alice",
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


async def test_ingest_accepts_a_collection_name(spec_instance: SpecStar):
    """A profile names its collections by ``name`` (manual §20); the capability
    resolves the name to the collection's id."""
    cid = (
        spec_instance.get_resource_manager(Collection)
        .create(Collection(name="kb-logs"))
        .resource_id
    )
    store = MemoryFileStore()
    await store.write("ws", "/a.md", b"log content")
    doc_id = await ingest_to_collection(
        spec_instance,
        _ingestor(spec_instance),
        store,
        workspace_id="ws",
        collection="kb-logs",  # a NAME, not the id
        path="a.md",
        user="u",
    )
    assert doc_id == encode_doc_id(cid, "a.md")  # resolved to the collection's id


async def test_unknown_collection_is_rejected(spec_instance: SpecStar):
    store = MemoryFileStore()
    await store.write("ws", "/a.md", b"x")
    with pytest.raises(CollectionNotFound):
        await ingest_to_collection(
            spec_instance,
            _ingestor(spec_instance),
            store,
            workspace_id="ws",
            collection="no-such",
            path="a.md",
            user="u",
        )


async def test_collection_has_gate_reads_landing_back():
    """check.collection_has passes only when the capability confirms the doc landed
    (manual §8) — a hard guarantee on the reliable side-effect."""
    from workspace_app.workflow.checks import collection_has

    landed = {("kb", "a.md")}

    async def checker(collection: str, path: str) -> bool:
        return (collection, path) in landed

    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", collection_checker=checker)
    ok = await collection_has("kb", "a.md")(wf, None)
    assert ok.ok
    missing = await collection_has("kb", "b.md")(wf, None)
    assert not missing.ok


async def test_collection_has_without_capability_fails_closed():
    from workspace_app.workflow.checks import collection_has

    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws")
    verdict = await collection_has("kb", "a.md")(wf, None)
    assert not verdict.ok and "capability" in verdict.reason


async def test_ingest_to_collection_without_capability_raises():
    """``wf.ingest_to_collection`` needs the capability wired by the run driver."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws")
    with pytest.raises(RuntimeError, match="needs a capability"):
        await wf.ingest_to_collection("kb", "a.md")
