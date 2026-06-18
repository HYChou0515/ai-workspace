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
from workspace_app.workflow.capabilities import (
    CollectionNotFound,
    create_context_card,
    ingest_to_collection,
)
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


# ── create_context_card capability (topic-hub P9, manual §8) ─────────────


def test_create_context_card_creates_a_card_with_derived_norm_keys(spec_instance: SpecStar):
    from workspace_app.kb.context_cards import lookup
    from workspace_app.resources.kb import ContextCard

    cid = _collection(spec_instance)
    card_id = create_context_card(
        spec_instance,
        collection=cid,
        keys=["M4", "Metal 4"],
        title="Metal 4",
        body="The fourth metal layer.",
        user="alice",
    )
    card = spec_instance.get_resource_manager(ContextCard).get(card_id).data
    assert card.collection_id == cid
    assert card.body == "The fourth metal layer."
    # norm_keys is the derived, indexed lookup surface — so lookup finds it exactly.
    assert lookup(spec_instance, cid, ["m4"])["m4"]  # exact-key membership
    assert not lookup(spec_instance, cid, ["m40"])["m40"]  # but not a longer key


def test_create_context_card_accepts_a_collection_name(spec_instance: SpecStar):
    cid = spec_instance.get_resource_manager(Collection).create(Collection(name="kb-x")).resource_id
    card_id = create_context_card(
        spec_instance, collection="kb-x", keys=["t"], title="", body="b", user="u"
    )
    from workspace_app.resources.kb import ContextCard

    assert spec_instance.get_resource_manager(ContextCard).get(card_id).data.collection_id == cid


def test_create_context_card_falls_back_to_title_when_no_usable_key(spec_instance: SpecStar):
    """Mirror the #106 author action: with no usable key, the title becomes the key
    so the card is still findable."""
    from workspace_app.kb.context_cards import lookup
    from workspace_app.resources.kb import ContextCard

    cid = _collection(spec_instance)
    card_id = create_context_card(
        spec_instance, collection=cid, keys=["   "], title="Reflow", body="b", user="u"
    )
    card = spec_instance.get_resource_manager(ContextCard).get(card_id).data
    assert card.keys == ["Reflow"]
    assert lookup(spec_instance, cid, ["reflow"])["reflow"]


def test_create_context_card_unknown_collection_is_rejected(spec_instance: SpecStar):
    with pytest.raises(CollectionNotFound):
        create_context_card(
            spec_instance, collection="no-such", keys=["x"], title="", body="b", user="u"
        )


async def test_wf_create_context_card_is_idempotent_on_rerun(spec_instance: SpecStar):
    """Through the handle, a re-run skips the already-committed card (the
    ``step_card/<key>`` receipt) — the capability runs once (manual §8/§9)."""
    calls: list[tuple] = []

    async def fake_create(collection, keys, title, body):
        calls.append((collection, tuple(keys), title))
        return f"context-card:{len(calls)}"

    store = MemoryFileStore()
    for _ in range(2):
        wf = WorkflowHandle(store=store, workspace_id="ws", create_card=fake_create)
        card_id = await wf.create_context_card("kb", ["M4"], title="Metal 4", body="b")
        assert card_id == "context-card:1"  # the cached receipt id, re-run included
    assert len(calls) == 1  # executed once; the re-run skipped


async def test_wf_create_context_card_without_capability_raises():
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws")
    with pytest.raises(RuntimeError, match="needs a capability"):
        await wf.create_context_card("kb", ["x"], title="t", body="b")
