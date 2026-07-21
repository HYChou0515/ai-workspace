"""CardGenCoordinator (#175) — the "自動 context card" background runner.

A scripted ``CardDrafter`` stands in for the classify LLM and returns canned
drafts, so the job's orchestration (read docs → dedup → classify vs existing →
artifact) is proven end-to-end without a model. The LLM-backed drafter is
tested separately against a fake ``ILlm``.
"""

from __future__ import annotations

import msgspec
from specstar import QB
from specstar.types import Binary, TaskStatus

from workspace_app.kb.card_drafter import NullCardDrafter
from workspace_app.kb.card_gen import (
    CardDraft,
    DescriptionQuestionDraft,
    DocDigest,
    TermQuestionDraft,
)
from workspace_app.kb.card_gen_coordinator import CardGenCoordinator
from workspace_app.kb.card_gen_sources import WIKI_ID_PREFIX
from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.doc_questions import open_questions_for_collections
from workspace_app.kb.reconcile import Reconciler, collection_wiki_text
from workspace_app.kb.wiki.store import _rid
from workspace_app.resources import Collection, ContextCard, SourceDoc, WikiPage, make_spec
from workspace_app.resources.kb import EMBED_DIM, ClusterMember


def _add_source(spec, collection_id: str, path: str, text: str) -> str:
    """A ready SourceDoc with the real natural-key id, as the Ingestor makes it."""
    rm = spec.get_resource_manager(SourceDoc)
    rev = rm.create(
        SourceDoc(
            collection_id=collection_id,
            path=path,
            content=Binary(data=text.encode()),
            text=text,
            status="ready",
        ),
        resource_id=encode_doc_id(collection_id, path),
    )
    return rev.resource_id


def _add_indexing_binary(spec, collection_id: str, path: str) -> str:
    """A just-uploaded binary doc as the upload fast-path leaves it: no extracted
    ``text`` yet and ``status="indexing"`` (the index job hasn't run)."""
    rm = spec.get_resource_manager(SourceDoc)
    rev = rm.create(
        SourceDoc(
            collection_id=collection_id,
            path=path,
            content=Binary(data=b"\x89PNG\r\n" + b"\xff\x00" * 64, content_type="image/png"),
            text=None,
            status="indexing",
        ),
        resource_id=encode_doc_id(collection_id, path),
    )
    return rev.resource_id


def _add_wiki(spec, collection_id: str, path: str, text: str) -> str:
    """An LLM wiki page with its real ``_rid`` id — what the picker submits when a
    reviewer picks a wiki page as a card-gen source (#415)."""
    rm = spec.get_resource_manager(WikiPage)
    rm.create(
        WikiPage(collection_id=collection_id, path=path, content=Binary(data=text.encode())),
        resource_id=_rid(collection_id, path),
    )
    return _rid(collection_id, path)


def _add_card(spec, collection_id: str, keys: list[str], body: str = "") -> str:
    rm = spec.get_resource_manager(ContextCard)
    rev = rm.create(
        ContextCard(
            collection_id=collection_id,
            keys=keys,
            norm_keys=derive_norm_keys(keys),
            body=body,
        )
    )
    return rev.resource_id


class _FakeDrafter:
    """Returns a canned digest per document path (keyed by ``doc_path``): cards
    plus, for #377, the term / description questions the reader raised."""

    def __init__(
        self,
        by_path: dict[str, list[CardDraft]],
        *,
        term_qs: dict[str, list[TermQuestionDraft]] | None = None,
        desc_qs: dict[str, list[DescriptionQuestionDraft]] | None = None,
        fail_paths: set[str] | None = None,
    ) -> None:
        self._by_path = by_path
        self._term_qs = term_qs or {}
        self._desc_qs = desc_qs or {}
        self._fail_paths = fail_paths or set()
        self.seen: list[str] = []
        self.seen_cids: list[str] = []

    def digest(self, *, doc_path: str, doc_text: str, collection_id: str = "") -> DocDigest:
        self.seen.append(doc_path)
        self.seen_cids.append(collection_id)
        if doc_path in self._fail_paths:
            raise RuntimeError(f"drafter gave up on {doc_path}")  # a post-failover give-up
        return DocDigest(
            cards=self._by_path.get(doc_path, []),
            term_questions=self._term_qs.get(doc_path, []),
            description_questions=self._desc_qs.get(doc_path, []),
        )


def _collection(spec, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _jobs(spec) -> list:
    """Every queued CardGenJob, narrowed to the model type for ty."""
    from workspace_app.kb.card_gen import CardGenJob

    out = []
    for r in spec.get_resource_manager(CardGenJob).list_resources(QB.all().build()):
        assert isinstance(r.data, CardGenJob)
        out.append(r.data)
    return out


async def test_generates_a_new_card_proposal_from_a_document():
    """Tracer: a run over one document drafts one card; with no existing card it
    lands on the job artifact as a NEW proposal carrying its source provenance."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "spec.md", "The reflow zone uses RZ3 heating.")
    drafter = _FakeDrafter(
        {
            "spec.md": [
                CardDraft(
                    keys=["RZ3", "Reflow Zone 3"],
                    title="Reflow Zone 3",
                    body="The third reflow zone.",
                    snippet="The reflow zone uses RZ3 heating.",
                )
            ]
        }
    )
    coord = CardGenCoordinator(spec, drafter)
    job_id = coord.enqueue(cid, [doc])
    await coord.aclose()

    assert coord.status(job_id) == TaskStatus.COMPLETED
    art = coord.proposals(job_id)
    assert len(art.proposals) == 1
    p = art.proposals[0]
    assert p.mode == "new"
    assert p.target_card_id is None
    assert p.keys == ["RZ3", "Reflow Zone 3"]
    assert p.title == "Reflow Zone 3"
    assert p.decision == "pending"
    assert len(p.provenance) == 1
    assert p.provenance[0].path == "spec.md"
    assert p.provenance[0].doc_id == doc
    assert p.provenance[0].snippet == "The reflow zone uses RZ3 heating."


async def test_a_still_indexing_doc_is_skipped_not_digested_to_nothing():
    """A binary doc still ``indexing`` has no extracted text yet, so digesting it
    now would silently produce 0 cards (the drafter sees empty text). The run
    SKIPS it — deferring to the auto-digest hook that fires when it finishes
    indexing — instead of drafting from nothing. The ready doc still generates."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    ready = _add_source(spec, cid, "ready.md", "RZ3 is the third reflow zone")
    pending = _add_indexing_binary(spec, cid, "pending.png")
    drafter = _FakeDrafter({"ready.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]})
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [ready, pending])
    await coord.aclose()

    assert coord.status(jid) == TaskStatus.COMPLETED  # the run still closes cleanly
    assert drafter.seen == ["ready.md"]  # the still-indexing doc was never digested
    assert [p.keys for p in coord.proposals(jid).proposals] == [["RZ3"]]  # only the ready doc


def _cards(spec, cid: str):
    rm = spec.get_resource_manager(ContextCard)
    return rm.list_resources((QB["collection_id"] == cid).build())


async def test_a_finalized_run_is_listed_for_review_then_leaves_the_queue_on_commit():
    """#415: once finalized, a run is a 'done' item in the collection's 待審核
    queue; committing its accepted proposals writes cards and resolves the run
    (status 'committed') so it drops out of the queue."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone")
    drafter = _FakeDrafter({"a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]})
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()

    pending = coord.pending_runs(cid)
    assert [s.run_id for s in pending] == [jid]
    assert pending[0].proposal_count == 1

    accepted = [
        msgspec.structs.replace(p, decision="accepted") for p in coord.proposals(jid).proposals
    ]
    coord.save_review(jid, accepted)
    coord.commit(jid)

    assert coord.pending_runs(cid) == []
    assert len(_cards(spec, cid)) == 1


async def test_finalize_writes_first_class_card_proposal_rows():
    """#511 P1: finalizing a run projects each kept proposal into its own
    ``CardProposal`` resource, addressed by the deterministic ``prop:{run}:{pid}``
    id (aligned with the reconcile ClusterMember), so the review inbox can page it
    at the DB. The nested ``CardGenRun.proposals`` list stays written too (P1 keeps
    it as a read-only fallback), so both agree on ids + content."""
    from workspace_app.kb.card_proposal import CardProposalStore

    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone")
    d2 = _add_source(spec, cid, "b.md", "RZ7 is the seventh reflow zone")
    drafter = _FakeDrafter(
        {
            "a.md": [CardDraft(keys=["RZ3"], title="RZ3", body="third", snippet="s3")],
            "b.md": [CardDraft(keys=["RZ7"], title="RZ7", body="seventh", snippet="s7")],
        }
    )
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [d1, d2])
    await coord.aclose()

    store = CardProposalStore(spec)
    assert store.count_active(cid) == 2
    rows = store.list_active(cid)
    by_id = {pid: cp for pid, cp in rows}
    # nested + first-class agree on ids + content
    nested = {p.id: p for p in coord.proposals(jid).proposals}
    assert set(by_id) == {f"prop:{jid}:{pid}" for pid in nested}
    for pid, p in nested.items():
        cp = by_id[f"prop:{jid}:{pid}"]
        assert cp.run_id == jid
        assert cp.collection_id == cid
        assert cp.keys == p.keys
        assert cp.title == p.title
        assert cp.body == p.body
        assert cp.decision == "pending"


async def test_save_review_on_a_vanished_run_is_a_noop():
    """#511 P2: saving a review for a run whose collection cascaded away is a clean
    no-op (nothing to upsert), never a raise."""
    spec = make_spec(default_user="u")
    coord = CardGenCoordinator(spec, _FakeDrafter({}))
    coord.save_review("nope", [])  # no run → no-op


async def test_dismiss_removes_a_run_from_review_without_writing_cards():
    """#415: dismissing a run resolves it (status 'dismissed') so it leaves the
    queue, and writes no cards."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 body")
    drafter = _FakeDrafter({"a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]})
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()

    assert coord.pending_runs(cid)
    coord.dismiss(jid)
    assert coord.pending_runs(cid) == []
    assert _cards(spec, cid) == []


async def test_review_resolution_is_idempotent():
    """Resolving a proposal is a one-way, at-most-once transition (#511 P2): once a
    proposal is committed, a re-commit writes no second card and a later whole-run
    dismiss can't yank it back — both skip the now-TERMINAL proposal (idempotency is
    per-proposal, no run.status gate)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "RZ3 is the third reflow zone")
    drafter = _FakeDrafter({"a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]})
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()

    accepted = [
        msgspec.structs.replace(p, decision="accepted") for p in coord.proposals(jid).proposals
    ]
    coord.save_review(jid, accepted)
    coord.commit(jid)  # accepted → committed, writes one card
    coord.commit(jid)  # proposal already committed → no second card
    coord.dismiss(jid)  # proposal already terminal → dismiss flips nothing

    assert len(_cards(spec, cid)) == 1
    assert coord.pending_runs(cid) == []


async def test_pending_runs_are_scoped_to_their_collection():
    """The 待審核 queue shows only THIS collection's runs (#415)."""
    spec = make_spec(default_user="u")
    c1, c2 = _collection(spec), _collection(spec)
    d1 = _add_source(spec, c1, "a.md", "RZ3 body")
    d2 = _add_source(spec, c2, "b.md", "RZ4 body")
    drafter = _FakeDrafter(
        {
            "a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")],
            "b.md": [CardDraft(keys=["RZ4"], title="RZ4", snippet="s")],
        }
    )
    coord = CardGenCoordinator(spec, drafter)
    j1 = coord.enqueue(c1, [d1])
    coord.enqueue(c2, [d2])
    await coord.aclose()

    assert [s.run_id for s in coord.pending_runs(c1)] == [j1]


async def test_a_selected_wiki_page_is_read_and_drafted_like_a_document():
    """#415: the picker can pick an LLM wiki page as a source. It's submitted with
    the ``wiki:`` type-tag (so a same-path doc stays distinct), so
    ``CardGenSources`` reads the page markdown and it drafts a card cited by the
    page path — mixed into the same ``doc_ids``."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    tagged_id = WIKI_ID_PREFIX + _add_wiki(spec, cid, "/index.md", "RZ3 is the third reflow zone.")
    drafter = _FakeDrafter(
        {"/index.md": [CardDraft(keys=["RZ3"], title="RZ3", body="Third zone.", snippet="RZ3…")]}
    )
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [tagged_id])
    await coord.aclose()

    art = coord.proposals(jid)
    assert len(art.proposals) == 1
    assert art.proposals[0].keys == ["RZ3"]
    assert art.proposals[0].provenance[0].path == "/index.md"
    assert art.proposals[0].provenance[0].doc_id == tagged_id


async def test_a_wiki_page_source_still_yields_reviewable_proposals():
    """Picking a wiki page as the source with a reconciler wired — the production
    combination, previously untested (the #415 wiki-source test built the
    coordinator WITHOUT one, and the wiki-suppression tests used a SourceDoc).
    Every key drafted off a wiki page is by construction present in that
    collection's wiki, so grading proposals against the wiki made this run
    ALWAYS produce zero reviewable cards while still reporting COMPLETED."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    tagged_id = WIKI_ID_PREFIX + _add_wiki(spec, cid, "/index.md", "RZ3 is the third reflow zone.")
    drafter = _FakeDrafter(
        {"/index.md": [CardDraft(keys=["RZ3"], title="RZ3", body="Third zone.", snippet="RZ3…")]}
    )
    coord = CardGenCoordinator(
        spec,
        drafter,
        reconciler=Reconciler(
            spec,
            _TagEmb(),
            cluster_tau=0.5,
            suppress_tau=1.01,  # never suppress via near-card — isolate the wiki axis
            update_tau=1.01,
            wiki_text=lambda c: collection_wiki_text(spec, c),
        ),
    )
    jid = coord.enqueue(cid, [tagged_id])
    await coord.aclose()

    assert coord.status(jid) == TaskStatus.COMPLETED
    assert [p.keys for p in coord.proposals(jid).proposals] == [["RZ3"]]


async def test_a_draft_already_fully_covered_by_an_existing_card_is_skipped():
    """#175 Q5: a term whose normalised keys are all already on an existing card
    is a complete duplicate — dropped, never surfaced for review."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _add_card(spec, cid, ["RZ3", "Reflow Zone 3"], body="already defined")
    doc = _add_source(spec, cid, "spec.md", "...")
    drafter = _FakeDrafter({"spec.md": [CardDraft(keys=["RZ3"], title="x", body="y", snippet="s")]})
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()
    assert coord.proposals(jid).proposals == []


async def test_a_draft_overlapping_an_existing_card_becomes_an_update():
    """#175 Q5: a draft that shares a key with an existing card but adds an alias
    is proposed as an UPDATE targeting that card, not a duplicate new card."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    target = _add_card(spec, cid, ["M4"], body="old")
    doc = _add_source(spec, cid, "spec.md", "...")
    drafter = _FakeDrafter(
        {"spec.md": [CardDraft(keys=["M4", "Metal 4"], title="Metal 4", body="new", snippet="s")]}
    )
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()
    (p,) = coord.proposals(jid).proposals
    assert p.mode == "update"
    assert p.target_card_id == target
    assert "Metal 4" in p.keys


async def test_drafts_sharing_a_key_across_documents_merge_into_one_proposal():
    """#205/#175: drafts from different documents that share a normalised key are
    deduped into one proposal — aliases unioned, provenance from BOTH documents."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "...")
    d2 = _add_source(spec, cid, "b.md", "...")
    drafter = _FakeDrafter(
        {
            "a.md": [CardDraft(keys=["RZ3"], title="t1", body="b1", snippet="from a")],
            "b.md": [
                CardDraft(keys=["RZ3", "Reflow Zone 3"], title="t2", body="b2", snippet="from b")
            ],
        }
    )
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [d1, d2])
    await coord.aclose()
    (p,) = coord.proposals(jid).proposals
    assert set(derive_norm_keys(p.keys)) == {"rz3", "reflow zone 3"}
    assert {pr.path for pr in p.provenance} == {"a.md", "b.md"}


async def test_digest_term_question_is_raised_as_an_open_doc_question():
    """#377: a term the digest couldn't define is persisted as an OPEN term
    question carrying its source doc — not hallucinated into a card."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "spec.md", "Uses the R7 recipe.")
    drafter = _FakeDrafter(
        {}, term_qs={"spec.md": [TermQuestionDraft(term="R7", question="What is the R7 recipe?")]}
    )
    coord = CardGenCoordinator(spec, drafter)
    coord.enqueue(cid, [doc])
    await coord.aclose()
    ((_qid, q),) = open_questions_for_collections(spec, [cid])
    assert q.kind == "term"
    assert q.term == "R7"
    assert q.question_text == "What is the R7 recipe?"
    assert q.source_doc_ids == [doc]


async def test_a_term_already_carded_is_not_raised_as_a_question():
    """#377 guardrail ①: the digest doesn't re-ask a term the collection already
    has a card for."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _add_card(spec, cid, ["R7"], body="the R7 reflow recipe")
    doc = _add_source(spec, cid, "spec.md", "Uses the R7 recipe.")
    drafter = _FakeDrafter({}, term_qs={"spec.md": [TermQuestionDraft(term="R7", question="?")]})
    coord = CardGenCoordinator(spec, drafter)
    coord.enqueue(cid, [doc])
    await coord.aclose()
    assert open_questions_for_collections(spec, [cid]) == []


async def test_digest_description_question_is_raised_with_its_quote():
    """#377: a passage the digest couldn't follow is persisted as a description
    question quoting the passage, bound to its source doc."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "spec.md", "uses M4 then CMP")
    drafter = _FakeDrafter(
        {},
        desc_qs={
            "spec.md": [
                DescriptionQuestionDraft(quote="uses M4 then CMP", question="Why skip the clean?")
            ]
        },
    )
    coord = CardGenCoordinator(spec, drafter)
    coord.enqueue(cid, [doc])
    await coord.aclose()
    ((_qid, q),) = open_questions_for_collections(spec, [cid])
    assert q.kind == "description"
    assert q.quote == "uses M4 then CMP"
    assert q.source_doc_id == doc


async def test_a_document_deleted_before_the_run_is_skipped_cleanly():
    """A doc removed between enqueue and consume reads as gone — skipped without
    calling the drafter or failing the run."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "gone.md", "...")
    drafter = _FakeDrafter({"gone.md": [CardDraft(keys=["X"], snippet="s")]})
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [doc])
    spec.get_resource_manager(SourceDoc).permanently_delete(doc)
    await coord.aclose()
    assert coord.status(jid) == TaskStatus.COMPLETED
    assert coord.proposals(jid).proposals == []
    assert drafter.seen == []


async def test_the_split_job_is_partitioned_by_collection():
    """#58/#414 cross-pod serialisation: enqueue queues ONE ``split`` job stamped
    with the collection id as its partition_key so a collection's runs serialise
    across consumers (consumer NOT started, so we inspect the queued split job)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord = CardGenCoordinator(spec, _FakeDrafter({}))
    coord.enqueue(cid, [doc])
    (job,) = _jobs(spec)
    assert job.payload.kind == "split"
    assert job.partition_key == cid
    assert job.payload.collection_id == cid
    assert job.payload.doc_ids == [doc]


async def test_an_uncertain_draft_keeps_its_confidence_flag():
    """#205/#175 信心標記: an uncertain draft surfaces with confident=False so the
    review UI can ⚠️ it and default it out of the commit."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    drafter = _FakeDrafter(
        {"a.md": [CardDraft(keys=["X"], title="X", body="?", confident=False, snippet="s")]}
    )
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()
    (p,) = coord.proposals(jid).proposals
    assert p.confident is False


async def test_a_draft_with_no_usable_key_is_dropped():
    """A draft whose keys are blank after normalisation could never be found by
    lookup, so it is dropped rather than proposed."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    drafter = _FakeDrafter({"a.md": [CardDraft(keys=["   "], title="blank", snippet="s")]})
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()
    assert coord.proposals(jid).proposals == []


async def test_status_is_pending_until_the_run_is_consumed():
    """The FE polls status: PENDING until a consumer drains the queue, then
    COMPLETED with the proposals on the artifact."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord = CardGenCoordinator(spec, _FakeDrafter({"a.md": [CardDraft(keys=["X"], snippet="s")]}))
    jid = coord.enqueue(cid, [doc])
    assert coord.status(jid) == TaskStatus.PENDING
    assert coord.proposals(jid).proposals == []
    await coord.aclose()
    assert coord.status(jid) == TaskStatus.COMPLETED


# ── #414: fan-out (split → per-doc process → finalize) ───────────────────────


async def test_a_multi_doc_run_fans_out_into_parallelisable_process_jobs():
    """#414: a run over ≥2 documents fans out into one ``process`` job per doc
    (partition_key=None → free cross-pod parallelism) plus a split + a single
    finalize (partition_key=collection so a collection's runs serialise)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    docs = [_add_source(spec, cid, f"{i}.md", "x") for i in range(3)]
    coord = CardGenCoordinator(
        spec, _FakeDrafter({f"{i}.md": [CardDraft(keys=[f"K{i}"], snippet="s")] for i in range(3)})
    )
    coord.enqueue(cid, docs)
    await coord.aclose()  # drain split → 3 process → finalize

    jobs = _jobs(spec)
    by_kind: dict[str, list] = {"split": [], "process": [], "finalize": []}
    for j in jobs:
        by_kind[j.payload.kind].append(j)
    assert len(by_kind["split"]) == 1
    assert len(by_kind["process"]) == 3  # one per document — the parallelism unit
    assert len(by_kind["finalize"]) == 1
    # the process jobs carry NO partition_key so they parallelise across pods…
    assert all(p.partition_key is None for p in by_kind["process"])
    assert {p.payload.doc_index for p in by_kind["process"]} == {0, 1, 2}
    # …while split + finalize serialise per collection.
    assert by_kind["split"][0].partition_key == cid
    assert by_kind["finalize"][0].partition_key == cid


async def test_a_single_doc_run_short_circuits_without_fanning_out():
    """#414: a ≤1-doc run digests inline in the split job — no process / finalize
    jobs — so the common auto-trigger path (one job per indexed doc) stays cheap."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord = CardGenCoordinator(spec, _FakeDrafter({"a.md": [CardDraft(keys=["X"], snippet="s")]}))
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()

    jobs = _jobs(spec)
    assert [j.payload.kind for j in jobs] == ["split"]  # split only — ran inline
    assert coord.status(jid) == TaskStatus.COMPLETED
    (p,) = coord.proposals(jid).proposals
    assert p.keys == ["X"]


async def test_the_drafter_is_told_the_documents_collection():
    # #506 P5: the agentic drafter scopes its ask_knowledge_base to the document's
    # OWN collection, so the coordinator must pass that collection id down to
    # `digest` — it isn't recoverable from doc_path / doc_text alone.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    drafter = _FakeDrafter({"a.md": [CardDraft(keys=["X"], snippet="s")]})
    coord = CardGenCoordinator(spec, drafter)
    coord.enqueue(cid, [doc])
    await coord.aclose()
    assert drafter.seen_cids == [cid]


async def test_set_drafter_swaps_in_a_new_drafter_after_construction():
    # #506 composition: create_app can only build the agentic drafter AFTER the KB
    # retriever + subagent bridge exist (both built after the coordinators), so it
    # swaps it into the already-constructed coordinator before any consumer starts.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord = CardGenCoordinator(spec, NullCardDrafter())  # starts with the no-op drafter
    coord.set_drafter(_FakeDrafter({"a.md": [CardDraft(keys=["SWAPPED"], snippet="s")]}))

    jid = coord.enqueue(cid, [doc])
    await coord.aclose()

    (p,) = coord.proposals(jid).proposals
    assert p.keys == ["SWAPPED"]  # the swapped-in drafter ran, not the null one


async def test_a_text_bearing_doc_that_digests_to_nothing_is_logged(caplog):
    """#494 observability: a doc that HAS text but produces 0 cards and 0
    questions on a COMPLETED run is the exact silent failure we could not
    attribute — it must WARN with the doc id and the text length, not pass
    unremarked."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "lots of real text here")
    coord = CardGenCoordinator(spec, _FakeDrafter({}))  # drafter yields an empty digest
    with caplog.at_level("WARNING"):
        jid = coord.enqueue(cid, [doc])
        await coord.aclose()

    assert coord.status(jid) == TaskStatus.COMPLETED  # still a green run…
    assert coord.proposals(jid).proposals == []  # …that produced nothing
    rec = next((r for r in caplog.records if "0 cards" in r.message), None)
    assert rec is not None and doc in rec.message  # …but now it is visible


async def test_finalize_logs_the_funnel_counts(caplog):
    """#494: one structured line at finalize records the whole funnel (units,
    drafts, proposals, questions, done/failed, final status) so a run that
    produced nothing is diagnosable end-to-end."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord = CardGenCoordinator(spec, _FakeDrafter({"a.md": [CardDraft(keys=["X"], snippet="s")]}))
    with caplog.at_level("INFO"):
        coord.enqueue(cid, [doc])
        await coord.aclose()

    rec = next((r for r in caplog.records if "finalize" in r.message.lower()), None)
    assert rec is not None
    assert "final_status=done" in rec.message and "n_proposals=1" in rec.message


async def test_a_doc_whose_digest_fails_does_not_sink_the_run():
    """#414 partial tolerance: one document the drafter gives up on is recorded
    failed, but the surviving documents' proposals still land and the run
    COMPLETEs — one bad doc no longer fails the whole run (was all-or-nothing)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "x")
    d2 = _add_source(spec, cid, "b.md", "y")
    drafter = _FakeDrafter(
        {"a.md": [CardDraft(keys=["RZ3"], title="t", snippet="s")]}, fail_paths={"b.md"}
    )
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [d1, d2])
    await coord.aclose()

    assert coord.status(jid) == TaskStatus.COMPLETED
    (p,) = coord.proposals(jid).proposals
    assert p.keys == ["RZ3"]  # a.md survived; b.md's failure did not sink the run


async def test_a_run_whose_every_doc_fails_is_marked_failed():
    """#414: when NO document could be digested the run has no proposals to show,
    so it ends FAILED (not a COMPLETED-with-nothing) — an honest failure signal."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "x")
    d2 = _add_source(spec, cid, "b.md", "y")
    coord = CardGenCoordinator(spec, _FakeDrafter({}, fail_paths={"a.md", "b.md"}))
    jid = coord.enqueue(cid, [d1, d2])
    await coord.aclose()

    assert coord.status(jid) == TaskStatus.FAILED
    assert coord.proposals(jid).proposals == []


async def test_reads_on_an_unknown_run_are_empty():
    """A bogus / vanished run id reads as an empty PENDING run and commits
    nothing — the routes never blow up on a stale id the FE still holds."""
    spec = make_spec(default_user="u")
    coord = CardGenCoordinator(spec, _FakeDrafter({}))
    assert coord.status("nope") == TaskStatus.PENDING
    assert coord.proposals("nope").proposals == []
    r = coord.commit("nope")
    assert (r.created, r.updated, r.skipped) == (0, 0, 0)


async def test_a_run_deleted_mid_fanout_is_skipped_by_its_process_jobs():
    """The run vanishing between split and the process jobs (a collection delete
    cascades it away) leaves nothing to digest — each process job finds no run and
    returns cleanly, draining without a crash or a stuck queue."""
    from workspace_app.kb.card_gen import CardGenRun

    spec = make_spec(default_user="u")
    cid = _collection(spec)
    docs = [_add_source(spec, cid, f"{i}.md", "x") for i in range(2)]
    drafter = _FakeDrafter({f"{i}.md": [CardDraft(keys=[f"K{i}"], snippet="s")] for i in range(2)})
    coord = CardGenCoordinator(spec, drafter)
    rid = coord.enqueue(cid, docs)
    spec.get_resource_manager(CardGenRun).permanently_delete(rid)  # e.g. collection cascade
    await coord.aclose()  # split fans out; the process jobs find no run and skip

    assert coord.proposals(rid).proposals == []


async def test_a_single_doc_run_whose_run_vanished_finalizes_cleanly():
    """The inline (short-circuit) finalize on a run that vanished mid-flight
    returns without writing proposals — the deleted-run race is a clean no-op."""
    from workspace_app.kb.card_gen import CardGenRun

    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    drafter = _FakeDrafter({"a.md": [CardDraft(keys=["X"], snippet="s")]})
    coord = CardGenCoordinator(spec, drafter)
    rid = coord.enqueue(cid, [doc])
    spec.get_resource_manager(CardGenRun).permanently_delete(rid)  # e.g. collection cascade
    await coord.aclose()

    assert coord.proposals(rid).proposals == []  # run gone → nothing to show, no crash


# ── Phase 2: review-state persistence + commit ───────────────────────────────


def _list_cards(spec, cid: str) -> list[ContextCard]:
    rm = spec.get_resource_manager(ContextCard)
    out = []
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        assert isinstance(r.data, ContextCard)
        out.append(r.data)
    return out


async def _run(spec, cid, by_path, docs):
    coord = CardGenCoordinator(spec, _FakeDrafter(by_path))
    jid = coord.enqueue(cid, docs)
    await coord.aclose()
    return coord, jid


async def test_save_review_persists_decisions_and_edits():
    """#175 Q7 resumable: the reviewer's decision + body edits are written back
    onto the (durable) job artifact, so a re-read restores them."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord, jid = await _run(
        spec, cid, {"a.md": [CardDraft(keys=["RZ3"], title="t", body="orig", snippet="s")]}, [doc]
    )
    (p,) = coord.proposals(jid).proposals
    p.decision = "accepted"
    p.body = "edited body"
    coord.save_review(jid, [p])

    (again,) = coord.proposals(jid).proposals
    assert again.decision == "accepted"
    assert again.body == "edited body"


async def test_commit_creates_a_card_for_an_accepted_new_proposal():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord, jid = await _run(
        spec,
        cid,
        {"a.md": [CardDraft(keys=["RZ3"], title="Reflow Zone 3", body="3rd zone", snippet="s")]},
        [doc],
    )
    (p,) = coord.proposals(jid).proposals
    p.decision = "accepted"
    coord.save_review(jid, [p])

    res = coord.commit(jid)
    assert res.created == 1 and res.updated == 0 and res.skipped == 0
    (card,) = _list_cards(spec, cid)
    assert card.keys == ["RZ3"]
    assert card.norm_keys == ["rz3"]
    assert card.body == "3rd zone"
    assert card.title == "Reflow Zone 3"


async def test_commit_overwrites_the_target_card_for_an_accepted_update():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    target = _add_card(spec, cid, ["M4"], body="old")
    doc = _add_source(spec, cid, "a.md", "x")
    coord, jid = await _run(
        spec,
        cid,
        {"a.md": [CardDraft(keys=["M4", "Metal 4"], title="Metal 4", body="new", snippet="s")]},
        [doc],
    )
    (p,) = coord.proposals(jid).proposals
    assert p.mode == "update" and p.target_card_id == target
    p.decision = "accepted"
    coord.save_review(jid, [p])

    res = coord.commit(jid)
    assert res.updated == 1 and res.created == 0
    cards = _list_cards(spec, cid)
    assert len(cards) == 1  # overwritten in place, not duplicated
    assert set(cards[0].norm_keys) == {"m4", "metal 4"}
    assert cards[0].body == "new"


async def test_commit_keeps_the_target_cards_reference_doc_ids():
    """#518 REGRESSION GUARD: committing a card-gen UPDATE rewrites the whole card
    struct. A proposal has no notion of linked documents, so the links a human curated
    onto the target must survive the overwrite — otherwise every card-gen round silently
    strips the evidence off the cards it refreshes."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    target = _add_card(spec, cid, ["M4"], body="old")
    rm = spec.get_resource_manager(ContextCard)
    curated = rm.get(target).data
    rm.create_or_update(target, msgspec.structs.replace(curated, reference_doc_ids=["doc-a"]))
    doc = _add_source(spec, cid, "a.md", "x")
    coord, jid = await _run(
        spec,
        cid,
        {"a.md": [CardDraft(keys=["M4", "Metal 4"], title="Metal 4", body="new", snippet="s")]},
        [doc],
    )
    (p,) = coord.proposals(jid).proposals
    assert p.mode == "update" and p.target_card_id == target
    p.decision = "accepted"
    coord.save_review(jid, [p])

    assert coord.commit(jid).updated == 1
    got = rm.get(target).data
    assert got.body == "new"  # the refresh landed…
    assert got.reference_doc_ids == ["doc-a"]  # …without stripping the curated links


async def test_commit_skips_proposals_the_reviewer_did_not_accept():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "x")
    d2 = _add_source(spec, cid, "b.md", "y")
    coord, jid = await _run(
        spec,
        cid,
        {
            "a.md": [CardDraft(keys=["A"], title="A", snippet="s")],
            "b.md": [CardDraft(keys=["B"], title="B", snippet="s")],
        },
        [d1, d2],
    )
    proposals = coord.proposals(jid).proposals
    proposals[0].decision = "rejected"  # the other stays "pending"
    coord.save_review(jid, proposals)

    res = coord.commit(jid)
    assert res.created == 0 and res.updated == 0
    assert res.skipped == len(proposals)
    assert _list_cards(spec, cid) == []


async def test_commit_falls_back_to_create_when_the_update_target_was_deleted():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    target = _add_card(spec, cid, ["M4"], body="old")
    doc = _add_source(spec, cid, "a.md", "x")
    coord, jid = await _run(
        spec,
        cid,
        {"a.md": [CardDraft(keys=["M4", "Metal 4"], title="Metal 4", body="new", snippet="s")]},
        [doc],
    )
    (p,) = coord.proposals(jid).proposals
    p.decision = "accepted"
    coord.save_review(jid, [p])
    spec.get_resource_manager(ContextCard).permanently_delete(target)  # vanishes pre-commit

    res = coord.commit(jid)
    assert res.created == 1 and res.updated == 0
    cards = _list_cards(spec, cid)
    assert any(set(c.norm_keys) == {"m4", "metal 4"} for c in cards)


async def test_commit_skips_an_accepted_proposal_with_no_usable_key():
    """A degenerate proposal the reviewer accepted but whose keys are all blank
    can't become a findable card — skipped, not created."""
    from workspace_app.kb.card_gen import ProposedCard

    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord, jid = await _run(
        spec, cid, {"a.md": [CardDraft(keys=["A"], title="A", snippet="s")]}, [doc]
    )
    coord.save_review(jid, [ProposedCard(keys=["  "], title="x", body="y", decision="accepted")])

    res = coord.commit(jid)
    assert res.created == 0 and res.skipped == 1
    assert _list_cards(spec, cid) == []


async def test_a_confident_draft_supersedes_an_uncertain_one_when_merged():
    """When two drafts of the same term merge, a confident draft's title/body
    wins over an earlier uncertain one's."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "x")
    d2 = _add_source(spec, cid, "b.md", "y")
    coord, jid = await _run(
        spec,
        cid,
        {
            "a.md": [
                CardDraft(keys=["RZ3"], title="guess", body="unsure", confident=False, snippet="sa")
            ],
            "b.md": [
                CardDraft(
                    keys=["RZ3"],
                    title="Reflow Zone 3",
                    body="definite",
                    confident=True,
                    snippet="sb",
                )
            ],
        },
        [d1, d2],
    )
    (p,) = coord.proposals(jid).proposals
    assert p.confident is True
    assert p.body == "definite"
    assert p.title == "Reflow Zone 3"


async def test_a_new_term_with_an_unrelated_existing_card_stays_new():
    """An existing card that shares no key is skipped over — the proposal is
    still classified NEW (exercises the no-overlap branch)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _add_card(spec, cid, ["ZZZ"], body="unrelated")
    doc = _add_source(spec, cid, "a.md", "x")
    coord, jid = await _run(
        spec, cid, {"a.md": [CardDraft(keys=["RZ3"], title="t", snippet="s")]}, [doc]
    )
    (p,) = coord.proposals(jid).proposals
    assert p.mode == "new"
    assert p.target_card_id is None


# ── #481: per-card decide + multi-card (cross-run) commit ────────────────────


async def test_proposals_carry_stable_ids():
    """#481: finalized proposals are addressable — each carries a stable id so the
    review table can act on one card."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "x")
    d2 = _add_source(spec, cid, "b.md", "y")
    coord, jid = await _run(
        spec,
        cid,
        {
            "a.md": [CardDraft(keys=["A"], snippet="s")],
            "b.md": [CardDraft(keys=["B"], snippet="s")],
        },
        [d1, d2],
    )
    ids = [p.id for p in coord.proposals(jid).proposals]
    assert all(ids) and len(set(ids)) == len(ids)  # non-empty and unique


async def test_decide_persists_a_single_cards_decision():
    """#481 inline accept/reject: ``decide`` flips one card's decision on the run;
    a re-read restores it (the run is the durable store)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "x")
    d2 = _add_source(spec, cid, "b.md", "y")
    coord, jid = await _run(
        spec,
        cid,
        {
            "a.md": [CardDraft(keys=["A"], snippet="s")],
            "b.md": [CardDraft(keys=["B"], snippet="s")],
        },
        [d1, d2],
    )
    first, second = coord.proposals(jid).proposals
    coord.decide(jid, first.id, "accepted")
    again = {p.id: p.decision for p in coord.proposals(jid).proposals}
    assert again[first.id] == "accepted"
    assert again[second.id] == "pending"


async def test_commit_cards_writes_only_the_referenced_cards():
    """#481: the multi-card commit writes exactly the referenced cards (not the
    whole run); a partial commit leaves the rest in the queue."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "x")
    d2 = _add_source(spec, cid, "b.md", "y")
    coord, jid = await _run(
        spec,
        cid,
        {
            "a.md": [CardDraft(keys=["A"], title="A", snippet="s")],
            "b.md": [CardDraft(keys=["B"], title="B", snippet="s")],
        },
        [d1, d2],
    )
    a, _b = coord.proposals(jid).proposals
    res = coord.commit_cards([(jid, a.id)])
    assert (res.created, res.updated, res.skipped) == (1, 0, 0)
    assert {c.keys[0] for c in _list_cards(spec, cid)} == {"A"}  # only A written
    assert coord.pending_runs(cid)  # B still pending → run stays in the queue


async def test_commit_cards_resolves_the_run_when_its_last_card_is_committed():
    """#481: committing a run's last active card settles it out of the queue."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord, jid = await _run(
        spec, cid, {"a.md": [CardDraft(keys=["A"], title="A", snippet="s")]}, [doc]
    )
    (a,) = coord.proposals(jid).proposals
    coord.commit_cards([(jid, a.id)])
    assert coord.pending_runs(cid) == []
    assert len(_list_cards(spec, cid)) == 1


async def test_commit_cards_spans_multiple_runs_in_one_call():
    """#481: a single commit can carry cards from different runs — per-run commit
    is just the special case where the refs share a run."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "x")
    d2 = _add_source(spec, cid, "b.md", "y")
    drafter = _FakeDrafter(
        {
            "a.md": [CardDraft(keys=["A"], title="A", snippet="s")],
            "b.md": [CardDraft(keys=["B"], title="B", snippet="s")],
        }
    )
    coord = CardGenCoordinator(spec, drafter)
    j1 = coord.enqueue(cid, [d1])
    j2 = coord.enqueue(cid, [d2])
    await coord.aclose()
    (a,) = coord.proposals(j1).proposals
    (b,) = coord.proposals(j2).proposals
    res = coord.commit_cards([(j1, a.id), (j2, b.id)])
    assert res.created == 2
    assert coord.pending_runs(cid) == []  # both runs settled


async def test_update_proposal_persists_a_drawer_edit():
    """#481 drawer edit: editing a card's body + decision persists on the run."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord, jid = await _run(
        spec, cid, {"a.md": [CardDraft(keys=["A"], body="old", snippet="s")]}, [doc]
    )
    (a,) = coord.proposals(jid).proposals
    coord.update_proposal(jid, a.id, msgspec.structs.replace(a, body="edited", decision="accepted"))
    (again,) = coord.proposals(jid).proposals
    assert again.id == a.id
    assert (again.body, again.decision) == ("edited", "accepted")


async def test_commit_cards_ignores_a_gone_or_resolved_run():
    """#481: refs to a vanished / already-resolved run are silently skipped — the
    multi-card commit never blows up on a stale id the FE still holds."""
    spec = make_spec(default_user="u")
    coord = CardGenCoordinator(spec, _FakeDrafter({}))
    res = coord.commit_cards([("nope", "0")])
    assert (res.created, res.updated, res.skipped) == (0, 0, 0)


async def test_commit_cards_skips_a_rejected_reference():
    """#481: a ref to a rejected card writes nothing — the reviewer's rejection
    wins over an accidental selection (the run stays open on its other card)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1 = _add_source(spec, cid, "a.md", "x")
    d2 = _add_source(spec, cid, "b.md", "y")
    coord, jid = await _run(
        spec,
        cid,
        {
            "a.md": [CardDraft(keys=["A"], title="A", snippet="s")],
            "b.md": [CardDraft(keys=["B"], title="B", snippet="s")],
        },
        [d1, d2],
    )
    a, _b = coord.proposals(jid).proposals
    coord.decide(jid, a.id, "rejected")  # B stays pending → run stays done
    res = coord.commit_cards([(jid, a.id)])
    assert (res.created, res.skipped) == (0, 1)
    assert _list_cards(spec, cid) == []


async def test_start_consuming_is_idempotent_and_takes_an_explicit_queue_factory():
    """create_app starts the consumer eagerly with the config-selected queue
    factory; the call is idempotent."""
    from specstar.message_queue import SimpleMessageQueueFactory

    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "a.md", "x")
    coord = CardGenCoordinator(
        spec,
        _FakeDrafter({"a.md": [CardDraft(keys=["X"], title="X", snippet="s")]}),
        message_queue_factory=SimpleMessageQueueFactory(),
    )
    assert coord.consuming is False
    coord.start_consuming()
    coord.start_consuming()  # idempotent — already consuming
    assert coord.consuming is True
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()
    assert coord.status(jid) == TaskStatus.COMPLETED


async def test_aclose_is_a_noop_when_nothing_was_enqueued():
    """aclose on a coordinator that never enqueued + never consumed returns
    immediately without spinning up a consumer thread."""
    spec = make_spec(default_user="u")
    coord = CardGenCoordinator(spec, _FakeDrafter({}))
    await coord.aclose()  # no raise, no work


class _TagEmb:
    """Deterministic fake embedder keyed by the text's LAST token (its title tag),
    so a coordinator test can decide semantic nearness without a real model (a hash
    embedder can't stand in for "M4 ≈ Metal 4"). Same tag → identical one-hot →
    cosine 1.0; different tag → orthogonal."""

    dim = EMBED_DIM
    identity = "tag"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._v(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._v(text)

    def _v(self, text: str) -> list[float]:
        import hashlib

        tag = text.split()[-1] if text.split() else ""
        bucket = int(hashlib.sha256(tag.encode()).hexdigest(), 16) % EMBED_DIM
        v = [0.0] * EMBED_DIM
        v[bucket] = 1.0
        return v


def _members(spec, cid: str) -> list[ClusterMember]:
    rm = spec.get_resource_manager(ClusterMember)
    out = []
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        assert isinstance(r.data, ClusterMember)
        out.append(r.data)
    return out


async def test_finalize_reconcile_suppresses_a_semantic_duplicate():
    """#506 P6 ⑥: with a reconciler wired in, a proposal that doesn't EXACTLY match
    an existing card (so the #175 exact classifier keeps it) but is semantically a
    duplicate is auto-suppressed — it never reaches the review queue, and an
    auditable suppressed ClusterMember records why."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    # existing card shares the title tag with the drafted card but a DIFFERENT key,
    # so classify_against_existing leaves the proposal "new" and the semantic layer
    # is what suppresses it.
    spec.get_resource_manager(ContextCard).create(
        ContextCard(
            collection_id=cid,
            keys=["alpha"],
            norm_keys=derive_norm_keys(["alpha"]),
            title="SharedTag",
        )
    )
    doc = _add_source(spec, cid, "d.md", "beta is explained here")
    drafter = _FakeDrafter({"d.md": [CardDraft(keys=["beta"], title="SharedTag", snippet="s")]})
    coord = CardGenCoordinator(
        spec,
        drafter,
        reconciler=Reconciler(spec, _TagEmb(), cluster_tau=0.5, suppress_tau=0.9, update_tau=0.7),
    )
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()

    assert coord.status(jid) == TaskStatus.COMPLETED
    assert coord.proposals(jid).proposals == []  # suppressed, not queued
    suppressed = [
        m for m in _members(spec, cid) if m.kind == "proposal" and m.state == "suppressed"
    ]
    assert len(suppressed) == 1
    assert suppressed[0].run_id == jid


async def test_finalize_reconcile_suppresses_a_wiki_explained_term_question():
    """#506 ③⑥: a raised term already explained in the collection's wiki is NOT
    opened as a question (so it is never re-asked) — it is recorded as an auditable
    suppressed ClusterMember instead. Wired to the REAL ``collection_wiki_text``,
    which also proves the sync wiki read works from the consumer-thread finalize
    path — the question path being the only one that still greps the wiki."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    _add_wiki(spec, cid, "/glossary.md", "The R7 recipe is fully documented in the wiki.")
    doc = _add_source(spec, cid, "spec.md", "Uses the R7 recipe.")
    drafter = _FakeDrafter(
        {}, term_qs={"spec.md": [TermQuestionDraft(term="R7", question="What is R7?")]}
    )
    coord = CardGenCoordinator(
        spec,
        drafter,
        reconciler=Reconciler(
            spec,
            _TagEmb(),
            cluster_tau=0.5,
            suppress_tau=1.01,  # never suppress via near-card — the wiki hit is the reason
            update_tau=1.01,
            wiki_text=lambda c: collection_wiki_text(spec, c),
        ),
    )
    coord.enqueue(cid, [doc])
    await coord.aclose()

    assert open_questions_for_collections(spec, [cid]) == []  # suppressed, never asked
    supp = [m for m in _members(spec, cid) if m.kind == "term_question" and m.state == "suppressed"]
    assert len(supp) == 1
    assert supp[0].reason == "wiki"
    assert supp[0].label == "R7"


async def test_finalize_reconcile_keeps_and_clusters_a_new_proposal():
    """#506 P6 ⑤: a genuinely-new proposal is kept and recorded as an active member
    with a cluster_key, so a later run's duplicate can be grouped with it."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    spec.get_resource_manager(ContextCard).create(
        ContextCard(
            collection_id=cid,
            keys=["alpha"],
            norm_keys=derive_norm_keys(["alpha"]),
            title="SharedTag",
        )
    )
    doc = _add_source(spec, cid, "d.md", "gamma is a new thing")
    drafter = _FakeDrafter({"d.md": [CardDraft(keys=["gamma"], title="OtherTag", snippet="s")]})
    coord = CardGenCoordinator(
        spec,
        drafter,
        reconciler=Reconciler(spec, _TagEmb(), cluster_tau=0.5, suppress_tau=0.9, update_tau=0.7),
    )
    jid = coord.enqueue(cid, [doc])
    await coord.aclose()

    kept = coord.proposals(jid).proposals
    assert [p.keys for p in kept] == [["gamma"]]
    active = [m for m in _members(spec, cid) if m.kind == "proposal" and m.state == "active"]
    assert len(active) == 1
    assert active[0].cluster_key == "gamma"


async def test_finalize_reconcile_clusters_a_term_question():
    """#506 P6 ⑤: a raised term question is projected as an active ClusterMember with
    a cluster_key, so the inbox (P7) can group it with a proposal for the same
    concept."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc = _add_source(spec, cid, "d.md", "A Widget appears here")
    drafter = _FakeDrafter(
        {"d.md": []},
        term_qs={"d.md": [TermQuestionDraft(term="Widget", question="What is a Widget?")]},
    )
    coord = CardGenCoordinator(
        spec,
        drafter,
        reconciler=Reconciler(spec, _TagEmb(), cluster_tau=0.5, suppress_tau=0.9, update_tau=0.7),
    )
    coord.enqueue(cid, [doc])
    await coord.aclose()

    tq = [m for m in _members(spec, cid) if m.kind == "term_question"]
    assert len(tq) == 1
    assert tq[0].state == "active"
    assert tq[0].cluster_key == "widget"  # norm(Widget) — opened its own cluster
