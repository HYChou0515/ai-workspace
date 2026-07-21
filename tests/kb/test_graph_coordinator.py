from collections.abc import Iterator
from types import SimpleNamespace

import msgspec
from specstar import QB
from specstar.types import Binary

from workspace_app.kb.graph.coordinator import GraphCoordinator
from workspace_app.kb.graph.jobs import GraphJob, GraphJobPayload
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim
from workspace_app.resources.kb import Collection, DocChunk, SourceDoc


class _FakeLlm(ILlm):
    """Stateless (thread-safe under parallel batch jobs). Answers whichever
    extractor asked, keyed on a word only that prompt contains — the batch runs
    both over the same chunks."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if "surface" in prompt:
            yield '[{"surface": "回焊爐", "kind": "機台"}]', False
        else:
            yield '[{"metric": "Revenue", "period": "Q3", "value": "1.2M", "unit": "USD"}]', False


def _mk_collection(spec, name: str, *, use_graph: bool, docs: list[tuple[str, str]]) -> str:
    coll_id = (
        spec.get_resource_manager(Collection)
        .create(Collection(name=name, use_graph=use_graph))
        .resource_id
    )
    drm = spec.get_resource_manager(SourceDoc)
    crm = spec.get_resource_manager(DocChunk)
    for doc_id, text in docs:
        # A real SourceDoc: since #534 slice 2 the extractor mirrors the deck's read
        # permission onto every claim, so the deck has to exist to be extracted from.
        with drm.using("bob"):
            drm.create(
                SourceDoc(
                    collection_id=coll_id,
                    path=f"{doc_id}.pptx",
                    content=Binary(data=b"x"),
                    collection_visibility="public",
                    collection_created_by="bob",
                ),
                resource_id=doc_id,
            )
        crm.create(
            DocChunk(collection_id=coll_id, source_doc_id=doc_id, seq=0, start=0, end=1, text=text)
        )
    return coll_id


def _claims(spec, collection_id: str) -> list[GraphClaim]:
    grm = spec.get_resource_manager(GraphClaim)
    out: list[GraphClaim] = []
    for r in grm.list_resources((QB["collection_id"] == collection_id).build()):
        assert isinstance(r.data, GraphClaim)
        out.append(r.data)
    return out


async def test_graph_fan_out_extracts_only_opted_in_collections():
    spec = make_spec()
    on = _mk_collection(spec, "reports", use_graph=True, docs=[("deck-A", "Q3 revenue 1.2M")])
    off = _mk_collection(spec, "photos", use_graph=False, docs=[("photo-1", "a cat")])

    coord = GraphCoordinator(spec, _FakeLlm(), batch_size=10)
    coord.enqueue_dispatch()
    coord.start_consuming()
    await coord.aclose()

    on_claims = _claims(spec, on)
    assert len(on_claims) == 1
    assert on_claims[0].metric == "Revenue"
    assert on_claims[0].source_doc_id == "deck-A"
    assert on_claims[0].norm_metric == "revenue"
    assert _claims(spec, off) == []  # use_graph=False collection is skipped


async def test_split_reconciles_the_permission_mirror_before_extracting():
    """#534 slice 2: claims written before the mirror existed carry no verdict at
    all, and the scope hides a claim whose mirror was never written. The split step
    re-pushes the collection's verdict — and each deck's own override on top — onto
    every claim it already holds, so the backfill rides the job that already runs
    weekly instead of a one-shot operator step, and any later drift heals itself.

    Cheap by construction: two bulk patches per collection, no LLM, and a patch
    that changes nothing writes no revision."""
    from workspace_app.perm import Permission
    from workspace_app.resources.graph import GraphClaim

    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec, "reports", use_graph=True, docs=[("deck-A", "Q3 revenue 1.2M")])
    # a deck tightened below its collection
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get("deck-A").data
    assert isinstance(doc, SourceDoc)
    with drm.using("bob"):
        drm.update(
            "deck-A",
            msgspec.structs.replace(
                doc, permission=Permission(visibility="restricted", read_content=["user:amy"])
            ),
        )
    # a pre-slice-2 claim: no mirror at all
    grm = spec.get_resource_manager(GraphClaim)
    with grm.using("bob"):
        legacy = grm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-A",
                norm_metric="revenue",
                metric="Revenue",
                value="1.2M",
            )
        ).resource_id

    coord = GraphCoordinator(spec, _FakeLlm(), batch_size=10)
    coord.reconcile_mirrors(cid)

    got = grm.get(legacy).data
    assert isinstance(got, GraphClaim)
    assert got.collection_visibility == "public"
    assert got.collection_created_by == "bob"
    assert got.doc_visibility == "restricted"
    assert got.doc_read_content == ["user:amy"]


async def test_the_split_job_runs_the_reconcile_before_fanning_out_batches():
    """The reconcile is only a backfill if the JOB runs it. Calling it directly in
    a test proves the function works and nothing about whether anything invokes it,
    so this asserts the wiring: a legacy claim is repaired by handling a split
    payload, not by a hand call."""
    from workspace_app.resources.graph import GraphClaim

    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec, "reports", use_graph=True, docs=[("deck-A", "Q3 revenue 1.2M")])
    grm = spec.get_resource_manager(GraphClaim)
    with grm.using("bob"):
        legacy = grm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-A",
                norm_metric="revenue",
                metric="Revenue",
                value="1.2M",
            )
        ).resource_id
    assert _mirror_of(spec, legacy).collection_visibility == ""

    coord = GraphCoordinator(spec, _FakeLlm(), batch_size=10)
    coord._handle(
        SimpleNamespace(data=GraphJob(payload=GraphJobPayload(kind="split", collection_id=cid)))
    )

    assert _mirror_of(spec, legacy).collection_visibility == "public"


async def test_the_reconcile_never_publishes_a_tightened_deck():
    """The un-overridden decks are excluded from the "no override" push rather than
    reset and re-tightened afterwards, so a restricted deck's claims are never
    written as public — not even for the instant between two commits. Asserted by
    watching what the reset is ASKED to touch, since the window it would open is a
    race no assertion after the fact can catch."""
    from workspace_app.perm import Permission
    from workspace_app.resources.graph import GraphClaim

    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(
        spec,
        "reports",
        use_graph=True,
        docs=[("deck-A", "Q3 revenue 1.2M"), ("deck-B", "Q3 revenue 9M")],
    )
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get("deck-A").data
    assert isinstance(doc, SourceDoc)
    with drm.using("bob"):
        drm.update(
            "deck-A",
            msgspec.structs.replace(doc, permission=Permission(visibility="private")),
        )
    grm = spec.get_resource_manager(GraphClaim)
    with grm.using("bob"):
        tightened = grm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-A",
                norm_metric="revenue",
                metric="Revenue",
                value="1.2M",
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="private",
            )
        ).resource_id

    seen: list[str] = []
    grm_patch = grm.patch_many

    def spy(query, patch, **kw):
        if patch.get("doc_visibility") == "public":
            seen.append("reset")
            # whatever the reset selects must NOT include the tightened deck
            assert _mirror_of(spec, tightened).doc_visibility == "private"
        return grm_patch(query, patch, **kw)

    grm.patch_many = spy  # ty: ignore[invalid-assignment]
    GraphCoordinator(spec, _FakeLlm(), batch_size=10).reconcile_mirrors(cid)
    assert seen == ["reset"]
    assert _mirror_of(spec, tightened).doc_visibility == "private"


def _mirror_of(spec, claim_id: str):
    from workspace_app.resources.graph import GraphClaim

    got = spec.get_resource_manager(GraphClaim).get(claim_id).data
    assert isinstance(got, GraphClaim)
    return got


async def test_the_batch_job_records_mentions_alongside_claims():
    """The primary layer rides the job that already exists. Asserted through the
    JOB rather than by calling the writer, because a writer nothing invokes
    produces nothing — and that failure is invisible in the writer's own tests."""
    from specstar import QB as _QB

    from workspace_app.resources.graph import GraphMention

    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec, "reports", use_graph=True, docs=[("deck-A", "回焊爐 溫度 250C")])
    coord = GraphCoordinator(spec, _FakeLlm(), batch_size=10)
    coord._handle(
        SimpleNamespace(
            data=GraphJob(
                payload=GraphJobPayload(kind="batch", collection_id=cid, doc_ids=["deck-A"])
            )
        )
    )
    mrm = spec.get_resource_manager(GraphMention)
    rows = list(mrm.list_resources((_QB["source_doc_id"] == "deck-A").build()))
    assert len(rows) == 1
    assert isinstance(rows[0].data, GraphMention)
    assert rows[0].data.surface == "回焊爐"


async def test_a_reconcile_job_builds_the_vocabulary():
    """The vocabulary pass is a JOB kind, not a sweep: it inherits the queue's
    retry, status and logging, and a worker pod can consume it like any other
    stage. Asserted through _handle so the wiring is what is under test."""
    from workspace_app.resources.graph import GraphEntity

    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec, "reports", use_graph=True, docs=[("deck-A", "回焊爐 250C")])
    coord = GraphCoordinator(spec, _FakeLlm(), batch_size=10)
    coord._handle(
        SimpleNamespace(
            data=GraphJob(
                payload=GraphJobPayload(kind="batch", collection_id=cid, doc_ids=["deck-A"])
            )
        )
    )
    coord._handle(SimpleNamespace(data=GraphJob(payload=GraphJobPayload(kind="reconcile"))))
    erm = spec.get_resource_manager(GraphEntity)
    names = {r.data.canonical_name for r in erm.list_resources(QB.all().build())}
    assert "回焊爐" in names


async def test_a_dispatch_ends_by_queueing_a_reconcile():
    """The pass that turns evidence into a vocabulary has to be asked for, or a
    corpus extracts every week and never gets an entity page."""
    spec = make_spec(default_user=lambda: "bob")
    _mk_collection(spec, "reports", use_graph=True, docs=[("deck-A", "x")])
    coord = GraphCoordinator(spec, _FakeLlm(), batch_size=10)
    coord._handle(SimpleNamespace(data=GraphJob(payload=GraphJobPayload(kind="dispatch"))))
    jrm = spec.get_resource_manager(GraphJob)
    kinds = [r.data.payload.kind for r in jrm.list_resources(QB.all().build())]
    assert "reconcile" in kinds
