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


def test_update_context_card_falls_back_to_title_when_no_usable_key(spec_instance: SpecStar):
    """Mirror create: a blank-keys edit keeps the card findable by reusing the title."""
    from workspace_app.kb.context_cards import lookup
    from workspace_app.resources.kb import ContextCard
    from workspace_app.workflow.capabilities import update_context_card

    cid = _collection(spec_instance)
    card_id = create_context_card(
        spec_instance, collection=cid, keys=["M4"], title="", body="b", user="u"
    )
    update_context_card(
        spec_instance, card_id=card_id, keys=["   "], title="Reflow", body="b2", user="u"
    )
    card = spec_instance.get_resource_manager(ContextCard).get(card_id).data
    assert card.keys == ["Reflow"]
    assert lookup(spec_instance, cid, ["reflow"])["reflow"]


# ── upsert_context_card capability (#111 — the workflow commit path) ──────


def test_upsert_context_card_falls_back_to_title_when_no_usable_key(spec_instance: SpecStar):
    from workspace_app.kb.context_cards import lookup
    from workspace_app.workflow.capabilities import upsert_context_card

    cid = _collection(spec_instance)
    upsert_context_card(spec_instance, collection=cid, keys=[""], title="Reflow", body="b", user="u")
    assert lookup(spec_instance, cid, ["reflow"])["reflow"]


def test_upsert_context_card_creates_when_no_card_has_the_key(spec_instance: SpecStar):
    from workspace_app.kb.context_cards import find_cards_by_key
    from workspace_app.workflow.capabilities import upsert_context_card

    cid = _collection(spec_instance)
    card_id = upsert_context_card(
        spec_instance, collection=cid, keys=["M4"], title="M4", body="four", user="u"
    )
    hits = find_cards_by_key(spec_instance, cid, "m4")
    assert [(i, c.body) for i, c in hits] == [(card_id, "four")]


def test_upsert_context_card_updates_the_existing_card_for_an_existing_key(spec_instance: SpecStar):
    """#111 ‘有就更新、沒才新增’: a second upsert for the same key overwrites the same
    card instead of creating a duplicate."""
    from workspace_app.kb.context_cards import find_cards_by_key
    from workspace_app.resources.kb import ContextCard
    from workspace_app.workflow.capabilities import upsert_context_card

    cid = _collection(spec_instance)
    first = upsert_context_card(
        spec_instance, collection=cid, keys=["M4"], title="M4", body="old", user="u"
    )
    second = upsert_context_card(
        spec_instance, collection=cid, keys=["M4"], title="M4", body="new", user="u"
    )
    assert second == first  # same card, updated
    assert len(find_cards_by_key(spec_instance, cid, "m4")) == 1  # no duplicate
    assert spec_instance.get_resource_manager(ContextCard).get(first).data.body == "new"


def test_upsert_context_card_unknown_collection_is_rejected(spec_instance: SpecStar):
    from workspace_app.workflow.capabilities import upsert_context_card

    with pytest.raises(CollectionNotFound):
        upsert_context_card(
            spec_instance, collection="no-such", keys=["x"], title="", body="b", user="u"
        )


async def test_wf_upsert_context_card_is_idempotent_on_rerun(spec_instance: SpecStar):
    """Through the handle, a re-run skips the already-committed card (the
    ``step_card/<key>`` receipt) — the capability runs once (manual §8/§9)."""
    calls: list[tuple] = []

    async def fake_upsert(collection, keys, title, body):
        calls.append((collection, tuple(keys), title))
        return f"context-card:{len(calls)}"

    store = MemoryFileStore()
    for _ in range(2):
        wf = WorkflowHandle(store=store, workspace_id="ws", upsert_card=fake_upsert)
        card_id = await wf.upsert_context_card("kb", ["M4"], title="Metal 4", body="b")
        assert card_id == "context-card:1"  # the cached receipt id, re-run included
    assert len(calls) == 1  # executed once; the re-run skipped


async def test_wf_upsert_context_card_without_capability_raises():
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws")
    with pytest.raises(RuntimeError, match="needs a capability"):
        await wf.upsert_context_card("kb", ["x"], title="t", body="b")


# ── update_context_card capability (#111, manual §8) ─────────────────────


def test_update_context_card_overwrites_by_id_and_re_derives_norm_keys(spec_instance: SpecStar):
    """#111: a full overwrite by id — new keys/title/body replace the old ones and
    ``norm_keys`` is re-derived so lookup follows the new keys, not the old."""
    from workspace_app.kb.context_cards import lookup
    from workspace_app.resources.kb import ContextCard
    from workspace_app.workflow.capabilities import update_context_card

    cid = _collection(spec_instance)
    card_id = create_context_card(
        spec_instance, collection=cid, keys=["M4"], title="Metal 4", body="old", user="alice"
    )
    update_context_card(
        spec_instance,
        card_id=card_id,
        keys=["M5", "Metal 5"],
        title="Metal 5",
        body="new body",
        user="bob",
    )
    card = spec_instance.get_resource_manager(ContextCard).get(card_id).data
    assert card.collection_id == cid  # collection is immutable across edits
    assert card.keys == ["M5", "Metal 5"]
    assert card.title == "Metal 5"
    assert card.body == "new body"
    assert lookup(spec_instance, cid, ["m5"])["m5"]  # follows the new keys
    assert not lookup(spec_instance, cid, ["m4"])["m4"]  # old key no longer resolves


def test_update_context_card_missing_id_raises_card_not_found(spec_instance: SpecStar):
    from workspace_app.workflow.capabilities import CardNotFound, update_context_card

    with pytest.raises(CardNotFound):
        update_context_card(
            spec_instance, card_id="no-such-card", keys=["x"], title="", body="b", user="u"
        )


def test_update_context_card_stale_expected_body_raises_conflict(spec_instance: SpecStar):
    """#111 concurrency guard: when the caller passes the body it believes is current
    and it no longer matches what's stored, the update is blocked — forcing the AI to
    re-read before overwriting."""
    from workspace_app.resources.kb import ContextCard
    from workspace_app.workflow.capabilities import CardConflict, update_context_card

    cid = _collection(spec_instance)
    card_id = create_context_card(
        spec_instance, collection=cid, keys=["M4"], title="", body="current", user="u"
    )
    with pytest.raises(CardConflict):
        update_context_card(
            spec_instance,
            card_id=card_id,
            keys=["M4"],
            title="",
            body="new",
            user="u",
            expected_body="STALE — not what is stored",
        )
    # the stored card is untouched by the blocked update
    assert spec_instance.get_resource_manager(ContextCard).get(card_id).data.body == "current"


def test_update_context_card_matching_expected_body_succeeds(spec_instance: SpecStar):
    from workspace_app.resources.kb import ContextCard
    from workspace_app.workflow.capabilities import update_context_card

    cid = _collection(spec_instance)
    card_id = create_context_card(
        spec_instance, collection=cid, keys=["M4"], title="", body="current", user="u"
    )
    update_context_card(
        spec_instance,
        card_id=card_id,
        keys=["M4"],
        title="",
        body="new",
        user="u",
        expected_body="current",  # matches → allowed
    )
    assert spec_instance.get_resource_manager(ContextCard).get(card_id).data.body == "new"
