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

from workspace_app.kb.card_gen import (
    CardDraft,
    DescriptionQuestionDraft,
    DocDigest,
    TermQuestionDraft,
)
from workspace_app.kb.card_gen_coordinator import CardGenCoordinator
from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.doc_questions import open_questions_for_collections
from workspace_app.kb.wiki.store import _rid
from workspace_app.resources import Collection, ContextCard, SourceDoc, WikiPage, make_spec


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

    def digest(self, *, doc_path: str, doc_text: str) -> DocDigest:
        self.seen.append(doc_path)
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
    """Resolving a run is a one-way, at-most-once transition (#415): once
    committed, a re-commit writes no second card and a later dismiss can't yank
    it back — both are no-ops from a non-``done`` state."""
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
    coord.commit(jid)  # done → committed, writes one card
    coord.commit(jid)  # committed run: guarded no-op, no second card
    coord.dismiss(jid)  # committed run: mark_dismissed is a no-op (not 'done')

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
    """#415: the picker can pick an LLM wiki page as a source. Its ``WikiPage`` id
    misses the SourceDoc lookup, so ``CardGenSources`` reads the page markdown and
    it drafts a card cited by the page path — mixed into the same ``doc_ids``."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    wiki_id = _add_wiki(spec, cid, "/index.md", "RZ3 is the third reflow zone.")
    drafter = _FakeDrafter(
        {"/index.md": [CardDraft(keys=["RZ3"], title="RZ3", body="Third zone.", snippet="RZ3…")]}
    )
    coord = CardGenCoordinator(spec, drafter)
    jid = coord.enqueue(cid, [wiki_id])
    await coord.aclose()

    art = coord.proposals(jid)
    assert len(art.proposals) == 1
    assert art.proposals[0].keys == ["RZ3"]
    assert art.proposals[0].provenance[0].path == "/index.md"
    assert art.proposals[0].provenance[0].doc_id == wiki_id


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
