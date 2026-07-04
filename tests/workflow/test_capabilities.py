"""The ingest_to_collection capability (manual §8) — use case 2's reliable commit
step: a workspace file lands in an existing KB collection, idempotently."""

import json
from collections.abc import Callable

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


def _enqueue_recorder() -> tuple[Callable[[str, str], bool], list[tuple[str, str]]]:
    """A stand-in for ``IndexCoordinator.enqueue`` that records the (doc_id,
    collection_id) pairs the capability hands it — so a test asserts the index job
    was queued without registering a real message-queue model (#234)."""
    calls: list[tuple[str, str]] = []

    def enqueue(doc_id: str, collection_id: str) -> bool:
        calls.append((doc_id, collection_id))
        return True

    return enqueue, calls


def _noop_enqueue(doc_id: str, collection_id: str) -> bool:
    return True


async def test_ingest_stores_an_indexing_doc_enqueues_and_writes_a_receipt(spec_instance: SpecStar):
    cid = _collection(spec_instance)
    store = MemoryFileStore()
    await store.write("ws", "/digest/a.md", b"# A\nhello world content")
    enqueue, enqueued = _enqueue_recorder()

    doc_id = await ingest_to_collection(
        spec_instance,
        _ingestor(spec_instance),
        store,
        workspace_id="ws",
        collection=cid,
        path="digest/a.md",
        user="alice",
        enqueue=enqueue,
    )

    assert doc_id == encode_doc_id(cid, "digest/a.md")
    doc = spec_instance.get_resource_manager(SourceDoc).get(doc_id).data
    # #234: the upload lands as ``indexing`` and the index job is ENQUEUED — the
    # capability never blocks on chunk+embed; a background consumer indexes it.
    assert doc.status == "indexing"
    assert enqueued == [(doc_id, cid)]
    # the receipt makes the deterministic node checkpointable on re-run (§9), and
    # lives under the run's journal folder (#136) — _default with no workflow wired
    receipt = json.loads(await store.read("ws", "/.workflow/_default/step_ingest/digest/a.md.done"))
    assert receipt["doc_id"] == doc_id


async def test_collection_has_doc_counts_an_indexing_upload_as_landed(spec_instance: SpecStar):
    """#234: ingest is async now (store + enqueue), so a freshly-uploaded doc is still
    ``indexing`` when the workflow verifies it landed — ``landed`` means the SourceDoc
    EXISTS in the collection (the deterministic upload succeeded), not that the background
    chunk+embed has finished."""
    from workspace_app.workflow.capabilities import collection_has_doc

    cid = _collection(spec_instance)
    store = MemoryFileStore()
    await store.write("ws", "/a.md", b"hello world content")
    doc_id = await ingest_to_collection(
        spec_instance,
        _ingestor(spec_instance),
        store,
        workspace_id="ws",
        collection=cid,
        path="a.md",
        user="u",
        enqueue=_noop_enqueue,
    )
    # no consumer ran, so the doc is still indexing — yet it has landed in the collection.
    assert spec_instance.get_resource_manager(SourceDoc).get(doc_id).data.status == "indexing"
    assert collection_has_doc(spec_instance, collection=cid, path="a.md") is True
    assert collection_has_doc(spec_instance, collection=cid, path="missing.md") is False
    assert collection_has_doc(spec_instance, collection="no-such", path="a.md") is False


async def test_ingest_receipt_lives_under_per_workflow_dir(spec_instance: SpecStar):
    """#136: the ingest receipt is a journal artifact, so it lands under the run's
    /.workflow/<workflow_id>/ folder — not scattered at the workspace root."""
    cid = _collection(spec_instance)
    store = MemoryFileStore()
    await store.write("ws", "/a.md", b"hello world content")
    await ingest_to_collection(
        spec_instance,
        _ingestor(spec_instance),
        store,
        workspace_id="ws",
        collection=cid,
        path="a.md",
        user="alice",
        journal_dir="/.workflow/memory",
        enqueue=_noop_enqueue,
    )
    assert await store.exists("ws", "/.workflow/memory/step_ingest/a.md.done")
    assert not await store.exists("ws", "/step_ingest/a.md.done")


async def test_ingest_is_idempotent(spec_instance: SpecStar):
    """Re-ingesting the same path upserts via encode_doc_id — no duplicate doc."""
    cid = _collection(spec_instance)
    store = MemoryFileStore()
    await store.write("ws", "/a.md", b"same bytes")
    ing = _ingestor(spec_instance)
    for _ in range(2):
        await ingest_to_collection(
            spec_instance,
            ing,
            store,
            workspace_id="ws",
            collection=cid,
            path="a.md",
            user="u",
            enqueue=_noop_enqueue,
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
        enqueue=_noop_enqueue,
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
            enqueue=_noop_enqueue,
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
    upsert_context_card(
        spec_instance, collection=cid, keys=[""], title="Reflow", body="b", user="u"
    )
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


def test_upsert_context_card_retries_on_parallel_conflict(spec_instance: SpecStar, monkeypatch):
    """#429 P5: the workflow card commit is optimistic — it reads the card body, updates
    with an expected_body guard, and retries on CardConflict (a parallel run moved it), so
    two runs upserting one card don't silently lost-update (consistency with update_entity)."""
    from workspace_app.resources.kb import ContextCard
    from workspace_app.workflow import capabilities as cap

    cid = _collection(spec_instance)
    first = cap.upsert_context_card(
        spec_instance, collection=cid, keys=["M4"], title="M4", body="v0", user="u"
    )
    real_update = cap.update_context_card
    calls = {"n": 0}

    def flaky(spec, **kw):
        calls["n"] += 1
        if calls["n"] == 1:  # first attempt loses the optimistic race
            raise cap.CardConflict(kw["card_id"])
        return real_update(spec, **kw)

    monkeypatch.setattr(cap, "update_context_card", flaky)
    second = cap.upsert_context_card(
        spec_instance, collection=cid, keys=["M4"], title="M4", body="v1", user="u"
    )
    assert second == first and calls["n"] == 2  # retried once, then landed
    assert spec_instance.get_resource_manager(ContextCard).get(first).data.body == "v1"

    def always_conflict(spec, **kw):
        raise cap.CardConflict(kw["card_id"])

    monkeypatch.setattr(cap, "update_context_card", always_conflict)
    with pytest.raises(cap.CardConflict):
        cap.upsert_context_card(
            spec_instance, collection=cid, keys=["M4"], title="M4", body="v2", user="u", retries=2
        )


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


# ── find_overwrite_target capability (#205 — the review "before" snapshot) ──


def test_find_overwrite_target_returns_the_card_an_upsert_would_replace(spec_instance: SpecStar):
    """#205: it resolves the SAME card ``upsert`` would overwrite (first key with a hit),
    with the count of cards sharing that key (1 = unambiguous)."""
    from workspace_app.workflow.capabilities import find_overwrite_target, upsert_context_card

    cid = _collection(spec_instance)
    card_id = upsert_context_card(
        spec_instance,
        collection=cid,
        keys=["M4", "Metal 4"],
        title="Metal 4 layer",
        body="b",
        user="u",
    )
    card, ambiguity = find_overwrite_target(spec_instance, collection=cid, keys=["M4"], title="M4")
    assert card is not None and card.title == "Metal 4 layer" and card.body == "b"
    assert sorted(card.keys) == ["M4", "Metal 4"]  # the real (un-narrowed) keys
    assert ambiguity == 1
    # sanity: it's the very card upsert targets
    from workspace_app.kb.context_cards import find_cards_by_key

    assert find_cards_by_key(spec_instance, cid, "m4")[0][0] == card_id


def test_find_overwrite_target_is_none_for_a_new_card(spec_instance: SpecStar):
    from workspace_app.workflow.capabilities import find_overwrite_target

    cid = _collection(spec_instance)
    card, ambiguity = find_overwrite_target(
        spec_instance, collection=cid, keys=["never"], title="never"
    )
    assert card is None and ambiguity == 0


def test_find_overwrite_target_falls_back_to_title_when_no_usable_key(spec_instance: SpecStar):
    """#205 mirror of ``upsert``'s title fallback: with no normalisable key, the title
    itself becomes the lookup key — so the review 'before' snapshot still resolves the
    card a keyless, title-only upsert would overwrite."""
    from workspace_app.workflow.capabilities import find_overwrite_target, upsert_context_card

    cid = _collection(spec_instance)
    upsert_context_card(
        spec_instance, collection=cid, keys=["Metal 4"], title="Metal 4", body="b", user="u"
    )
    card, ambiguity = find_overwrite_target(spec_instance, collection=cid, keys=[], title="Metal 4")
    assert card is not None and card.title == "Metal 4"
    assert ambiguity == 1


def test_find_overwrite_target_counts_ambiguous_keys(spec_instance: SpecStar):
    """Two cards sharing a key (keys are many-to-many, no uniqueness) → ambiguity 2; only
    the first is the upsert target, surfaced so the overwrite isn't silently to one of N."""
    from workspace_app.workflow.capabilities import create_context_card, find_overwrite_target

    cid = _collection(spec_instance)
    create_context_card(spec_instance, collection=cid, keys=["dup"], title="A", body="a", user="u")
    create_context_card(spec_instance, collection=cid, keys=["dup"], title="B", body="b", user="u")
    card, ambiguity = find_overwrite_target(
        spec_instance, collection=cid, keys=["dup"], title="dup"
    )
    assert card is not None and ambiguity == 2


async def test_wf_find_overwrite_card_without_capability_returns_none():
    """An unwired handle (no driver) finds nothing — a fresh workspace diffs as all-new."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws")
    assert await wf.find_overwrite_card("kb", ["x"], title="t") is None


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
