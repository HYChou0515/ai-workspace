"""End-to-end index fan-out (#227): a large single-parser doc is split into many
small ``process`` jobs (one per unit batch), each parses+chunks+embeds its own
slice, and a single ``finalize`` job rejoins the text and flips the doc to ready.

Driven through the real ``IndexCoordinator`` + ``Ingestor`` + LI pipeline +
deterministic ``HashEmbedder`` (no LLM), so it exercises the actual queue/handler
dispatch, not a mock. CSV is the cheapest fan-out fixture — one row = one unit,
no VLM — but the machinery is parser-agnostic.
"""

from __future__ import annotations

from specstar import QB, SpecStar
from specstar.types import MergePatch, TaskStatus

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.index_coordinator import IndexCoordinator
from workspace_app.kb.index_jobs import IndexJob
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers import IParser
from workspace_app.resources import Collection, DocChunk, IndexUnitText, SourceDoc, make_spec
from workspace_app.resources.kb import EMBED_DIM, IndexRun


class _RecordingWiki:
    def __init__(self) -> None:
        self.hooked: list[str] = []

    async def on_doc_indexed(self, doc_id: str, *, requested_by: str | None = None) -> None:
        self.hooked.append(doc_id)


def _build(spec: SpecStar, wiki=None, *, csv_batch: int = 2):
    embedder = HashEmbedder(dim=EMBED_DIM)
    ingestor = Ingestor(spec, pipeline=build_doc_pipeline(embedder=embedder), embedder=embedder)
    coord = IndexCoordinator(
        spec,
        ingestor,
        wiki_coordinator=wiki,
        unit_batch_sizes={"CsvParser": csv_batch},
    )
    return ingestor, coord


def _store_csv(ingestor: Ingestor, cid: str, rows: int) -> str:
    body = "name\n" + "".join(f"r{i}\n" for i in range(rows))
    (doc_id,) = ingestor.store(
        collection_id=cid, user="u", filename="people.csv", data=body.encode()
    )
    return doc_id


def _chunks(spec: SpecStar, doc_id: str) -> list[DocChunk]:
    rm = spec.get_resource_manager(DocChunk)
    return [r.data for r in rm.list_resources((QB["source_doc_id"] == doc_id).build())]  # ty: ignore[invalid-return-type]


async def test_fanout_dedups_identical_multiunit_content_instead_of_rechunking():
    # #104: the alias dedup gate lives in index() / copy_from_cache, NOT the
    # fan-out planner. A multi-unit doc (CSV = many units) enqueued DIRECTLY —
    # e.g. a reindex after the #390 cache was invalidated, or a concurrent upload
    # that missed the cache — must still alias to a sibling that already owns the
    # identical content, not fan out into a second (duplicate) chunk set. This is
    # exactly the weekly-slide-deck case (multi-page PPTX/PDF) #104 targets.
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    body = ("name\n" + "".join(f"r{i}\n" for i in range(5))).encode()
    ingestor, coord = _build(spec, csv_batch=2)
    (owner,) = ingestor.store(collection_id=cid, user="u", filename="wk1/people.csv", data=body)
    (alias,) = ingestor.store(collection_id=cid, user="u", filename="wk2/people.csv", data=body)

    # Index the owner whole+synchronously first, so its chunks exist BEFORE the
    # alias's split runs the gate (avoids the concurrent-first-index race).
    ingestor.index(owner)
    assert len(_chunks(spec, owner)) == 5  # owner owns one chunk per row

    coord.enqueue(alias, cid)
    await coord.aclose()  # the alias's split must DEDUP via the fan-out gate

    assert _chunks(spec, alias) == []  # deduped: no duplicate chunk set fanned out
    docs = spec.get_resource_manager(SourceDoc)
    da, db = docs.get(owner).data, docs.get(alias).data
    assert da.status == "ready" and db.status == "ready"
    assert db.text == da.text  # the alias carries the extracted text


async def test_large_csv_fans_out_then_finalizes_to_ready():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    wiki = _RecordingWiki()
    ingestor, coord = _build(spec, wiki, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=5)  # 5 rows / batch 2 → 3 process jobs

    coord.enqueue(doc_id, cid)
    await coord.aclose()  # drain split → 3 process → finalize

    # One chunk per row survived the fan-out (each process job wrote its slice).
    chunks = _chunks(spec, doc_id)
    assert len(chunks) == 5
    assert {c.text.strip() for c in chunks} == {f"name: r{i}" for i in range(5)}

    # The doc finalized to ready, its text rejoined from every batch in order.
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert doc.status == "ready"
    assert doc.text is not None and "name: r0" in doc.text and "name: r4" in doc.text

    # The join state closed out, and the transient staging was cleaned up.
    run = spec.get_resource_manager(IndexRun).get(doc_id).data
    assert (run.total, sorted(run.done), run.failed, run.status) == (3, [0, 1, 2], [], "done")
    # #248: the progress aggregate covered every unit (5 rows across the 3 batches).
    assert (run.units_total, run.units_done) == (5, 5)
    staged = spec.get_resource_manager(IndexUnitText).list_resources(
        (QB["doc_id"] == doc_id).build()
    )
    assert list(staged) == []

    assert wiki.hooked == [doc_id]  # the wiki hook ran exactly once, after finalize

    # #390: finalize snapshotted the fanned-out result into the cross-path cache.
    from workspace_app.kb.index_cache import IndexCacheStore

    assert IndexCacheStore(spec).get(ingestor.cache_key(doc_id)) is not None


async def test_fanout_jobs_and_chunks_are_credited_to_the_requester():
    """#186: a large doc fans out into process + finalize IndexJobs created BY the
    worker (no request user). Those derived jobs — and every chunk they write —
    are credited to the run's requester, propagated from the split job's
    created_by, not the bare worker default. The SourceDoc stays its own owner."""
    who = {"u": "alice"}
    spec = make_spec(default_user=lambda: who["u"])
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    # alice uploads the doc (the content owner)…
    body = "name\n" + "".join(f"r{i}\n" for i in range(5))
    (doc_id,) = ingestor.store(
        collection_id=cid, user="alice", filename="people.csv", data=body.encode()
    )
    # …bob triggers the (re)index in HIS request → the split job's created_by is bob.
    who["u"] = "bob"
    coord.enqueue(doc_id, cid)
    # the worker drains split → 3 process → finalize with no request user.
    who["u"] = "index-worker"
    await coord.aclose()

    # SourceDoc stays alice (content owner, #83).
    assert spec.get_resource_manager(SourceDoc).get(doc_id).info.updated_by == "alice"
    # Every IndexJob in the fan-out (split + process + finalize) is credited to bob.
    jrm = spec.get_resource_manager(IndexJob)
    jobs = list(jrm.list_resources(QB["status"].eq(TaskStatus.COMPLETED).build()))
    assert len(jobs) >= 5  # 1 split + 3 process + 1 finalize
    assert {j.info.created_by for j in jobs} == {"bob"}  # ty: ignore[unresolved-attribute]
    # …and every chunk the process jobs wrote is credited to bob too.
    chrm = spec.get_resource_manager(DocChunk)
    chunks = list(chrm.list_resources((QB["source_doc_id"] == doc_id).build()))
    assert len(chunks) == 5
    assert {c.info.created_by for c in chunks} == {"bob"}  # ty: ignore[unresolved-attribute]


async def test_small_doc_takes_the_single_job_path_no_run_row():
    """A doc with one unit (here a tiny CSV: 1 row) is indexed whole — no
    IndexRun, no process/finalize jobs — exactly the pre-#227 behaviour."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=1)

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert len(_chunks(spec, doc_id)) == 1
    assert spec.get_resource_manager(SourceDoc).get(doc_id).data.status == "ready"
    assert coord._runs.get(doc_id) is None  # noqa: SLF001 — never fanned out, no run row


async def test_reenqueue_while_a_run_is_active_coalesces():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=5)
    # Simulate an in-flight fan-out: a running IndexRun for this doc.
    coord._runs.start(doc_id, cid, total=3)  # noqa: SLF001 — test drives the guard directly
    assert coord.enqueue(doc_id, cid) is False  # coalesced — no second fan-out


# ── safety sweep (#227 P6) ───────────────────────────────────────────


async def test_sweep_recovers_a_lost_finalize_trigger():
    """All batches recorded done but the finalize trigger was lost (its winner
    crashed before enqueuing). The sweep re-drives finalize even with a huge
    grace, because the gate condition is already met."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=3)
    runs = coord._runs  # noqa: SLF001
    runs.start(doc_id, cid, total=2)
    runs.mark_done(doc_id, 0)
    runs.mark_done(doc_id, 1)  # gate met; finalized still False; no finalize job exists

    assert coord.sweep_stuck_runs(stuck_after_seconds=99999) == [doc_id]
    await coord.aclose()  # drain the finalize job the sweep enqueued

    assert spec.get_resource_manager(SourceDoc).get(doc_id).data.status == "ready"
    assert runs.get(doc_id).status == "done"


async def test_sweep_skips_a_run_whose_doc_was_deleted():
    """#186: a run can outlive its doc (deleted mid fan-out). The sweep has no
    requester to recover and nothing to finalize, so it skips the run — never
    enqueueing a user-less finalize on the no-default job manager."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=3)
    runs = coord._runs  # noqa: SLF001
    runs.start(doc_id, cid, total=2)
    runs.mark_done(doc_id, 0)
    runs.mark_done(doc_id, 1)  # gate met, but…
    spec.get_resource_manager(SourceDoc).permanently_delete(doc_id)  # …the doc is gone

    assert coord.sweep_stuck_runs(stuck_after_seconds=99999) == []  # skipped, not recovered
    jrm = spec.get_resource_manager(IndexJob)
    assert list(jrm.list_resources(QB["status"].eq(TaskStatus.PENDING).build())) == []


async def test_sweep_fails_a_dead_lettered_batch_only_after_grace():
    """A batch that gave up (dead-lettered) never records done, so the gate
    never fills. The sweep leaves it alone while it might still be progressing,
    then — past the grace — records the missing batch failed and finalizes to
    error."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=3)
    runs = coord._runs  # noqa: SLF001
    runs.start(doc_id, cid, total=2)
    runs.mark_done(doc_id, 0)  # batch 1 dead-lettered — never recorded

    # Within grace → not touched (could still be in flight / retrying).
    assert coord.sweep_stuck_runs(stuck_after_seconds=99999) == []
    # Past grace → the missing batch is recorded failed and the run finalizes.
    assert coord.sweep_stuck_runs(stuck_after_seconds=0) == [doc_id]
    await coord.aclose()

    run = runs.get(doc_id)
    assert run.failed == [1] and run.status == "error"
    assert spec.get_resource_manager(SourceDoc).get(doc_id).data.status == "error"


async def test_sweep_redrives_finalize_claimed_but_lost():
    """The trigger winner crashed AFTER claiming the gate but BEFORE the finalize
    job ran (flag set, run still 'running'). claim_finalize now returns False, so
    only the aged finalized-but-running branch can recover it."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=3)
    runs = coord._runs  # noqa: SLF001
    runs.start(doc_id, cid, total=1)
    runs.mark_done(doc_id, 0)
    assert runs.claim_finalize(doc_id) is True  # winner claims… then "crashes"

    assert coord.sweep_stuck_runs(stuck_after_seconds=99999) == []  # within grace → wait
    assert coord.sweep_stuck_runs(stuck_after_seconds=0) == [doc_id]  # aged → re-drive
    await coord.aclose()
    assert runs.get(doc_id).status == "done"


async def test_process_and_finalize_are_noops_when_the_doc_was_deleted():
    """A doc deleted mid fan-out: the process job finds no updater and bails; a
    finalize finds the doc gone, clears its staging, and returns without raising."""
    from workspace_app.kb.index_jobs import IndexJobPayload

    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=3)
    coord._runs.start(doc_id, cid, total=1)  # noqa: SLF001
    coord._stage_text(doc_id, 0, "stale text")  # noqa: SLF001
    spec.get_resource_manager(SourceDoc).permanently_delete(doc_id)

    # process: no updater (doc gone) → returns without touching anything.
    coord._handle_process(  # noqa: SLF001
        IndexJobPayload(doc_id=doc_id, collection_id=cid, kind="process", unit_start=0, unit_end=1),
        "bob",  # #186: requester (unused here — the doc is gone, so it bails first)
    )
    # finalize: run exists but doc is gone → clears staging, no crash.
    coord._handle_finalize(  # noqa: SLF001
        IndexJobPayload(doc_id=doc_id, collection_id=cid, kind="finalize"), "bob"
    )
    staged = spec.get_resource_manager(IndexUnitText).list_resources(
        (QB["doc_id"] == doc_id).build()
    )
    assert list(staged) == []


async def test_finalize_is_idempotent_does_not_wipe_text():
    """Re-running finalize after the run is closed must NOT re-read the (now
    empty) staging and blank SourceDoc.text."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=5)
    coord.enqueue(doc_id, cid)
    await coord.aclose()
    text_before = spec.get_resource_manager(SourceDoc).get(doc_id).data.text
    assert text_before  # the normal fan-out populated it

    from workspace_app.kb.index_jobs import IndexJobPayload

    coord._handle_finalize(  # noqa: SLF001
        IndexJobPayload(doc_id=doc_id, collection_id=cid, kind="finalize"), "bob"
    )
    assert spec.get_resource_manager(SourceDoc).get(doc_id).data.text == text_before


# ── #248: a fan-out batch must NOT clobber the shared status_detail ────


class _ProgressParser(IParser):
    """A parser that, IF handed an on_progress sink, writes a per-page string —
    exactly the racy write a fan-out must suppress (N parallel batches would each
    overwrite the one status_detail field, making the old bar jump backward)."""

    def matches(self, *, filename, mime, source) -> bool:
        return True

    def count_units(self, source, *, filename, mime) -> int:
        return 1

    def parse(self, source, *, filename, mime, on_progress=None, on_preview=None, unit_range=None):
        from llama_index.core.schema import Document

        if on_progress is not None:
            on_progress("page 99/99")  # the racy write under test
        return [Document(text="hello world")]


async def test_fanout_index_units_does_not_write_per_page_status_detail():
    from workspace_app.kb.parsers.registry import ParserRegistry

    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    emb = HashEmbedder(dim=EMBED_DIM)
    reg = ParserRegistry()
    reg.register(_ProgressParser())
    ing = Ingestor(
        spec, pipeline=build_doc_pipeline(embedder=emb), embedder=emb, parser_registry=reg
    )
    (doc_id,) = ing.store(collection_id=cid, user="u", filename="a.txt", data=b"hello world")

    ing.index_units(doc_id, (0, 1), seq_base=0)  # one fan-out batch

    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert doc.status_detail == ""  # the per-page progress write is suppressed on fan-out


# ── #249: transient vs permanent failure of a fan-out process job ─────


class _Status(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _FailEmbedder:
    """An embedder whose every embed_documents call raises ``error``."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self._dim = EMBED_DIM
        self.calls = 0

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def identity(self) -> str:
        return f"fail-{self._dim}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        raise self._error

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self._dim


def _build_failing(spec: SpecStar, error: Exception):
    emb = _FailEmbedder(error)
    ingestor = Ingestor(spec, pipeline=build_doc_pipeline(embedder=emb), embedder=emb)
    coord = IndexCoordinator(
        spec, ingestor, wiki_coordinator=None, unit_batch_sizes={"CsvParser": 1}
    )
    return emb, ingestor, coord


async def test_fanout_permanent_error_deadletters_each_batch_without_retry():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    emb, ingestor, coord = _build_failing(spec, _Status(400))  # a bad request never recovers
    doc_id = _store_csv(ingestor, cid, rows=2)  # 2 rows / batch 1 → 2 process jobs

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert emb.calls == 2  # each batch embedded once — NoRetry dead-lettered it, no requeue


async def test_fanout_transient_error_is_redelivered_by_the_broker():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    emb, ingestor, coord = _build_failing(spec, _Status(503))  # transient — broker retries
    doc_id = _store_csv(ingestor, cid, rows=2)  # 2 process jobs

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert emb.calls > 2  # each batch was re-delivered (more calls than batches)


async def test_a_process_job_replayed_after_finalize_is_a_noop():
    """plan-rag-context P12 (round 4): at-least-once delivery can replay a batch
    AFTER finalize. P8 made a batch's chunk write provisional (batch-relative
    offsets, rebased at finalize), so a replay rewrote its chunks batch-relative
    and staged a stale text row — with the run `done`, nothing would ever rebase
    them again. The run says the work is done: the replay writes nothing."""
    from workspace_app.kb.index_jobs import IndexJobPayload

    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=5)
    coord.enqueue(doc_id, cid)
    await coord.aclose()
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc) and doc.text is not None
    before = sorted((c.seq, c.start, c.end) for c in _chunks(spec, doc_id))
    assert all(
        doc.text[s:e] == c.text
        for c in _chunks(spec, doc_id)
        for (_, s, e) in [(c.seq, c.start, c.end)]
    )
    coord._handle_process(
        IndexJobPayload(
            doc_id=doc_id,
            collection_id=cid,
            kind="process",
            unit_start=2,
            unit_end=4,
            batch_index=1,
        ),
        "u",
    )
    assert sorted((c.seq, c.start, c.end) for c in _chunks(spec, doc_id)) == before
    staged = spec.get_resource_manager(IndexUnitText).list_resources(
        (QB["doc_id"] == doc_id).build()
    )
    assert list(staged) == []


async def test_a_batch_replayed_while_finalize_runs_writes_nothing():
    """plan-rag-context P15→P17 (rounds 5–7): the P12 guard is check-then-act.
    A duplicate delivery that passed it (run still `running`) and whose chunk
    write lands AFTER finalize rebased the batch — its embedding outlived the
    other batches and the finalize — used to put that batch back to
    batch-relative offsets. The rows are create-only now: the duplicate's
    writes are refused row by row, finalize stays the only writer of offsets."""
    from workspace_app.kb.index_jobs import IndexJobPayload

    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=5)  # 3 batches: (0,2) (2,4) (4,5)

    def job(kind: str, b: int = 0, s: int = 0, e: int = 0) -> IndexJobPayload:
        return IndexJobPayload(
            doc_id=doc_id, collection_id=cid, kind=kind, unit_start=s, unit_end=e, batch_index=b
        )

    coord._handle_split(job("split"), "u", 0, 0)
    coord._handle_process(job("process", 0, 0, 2), "u")
    coord._handle_process(job("process", 1, 2, 4), "u")
    coord._handle_process(job("process", 2, 4, 5), "u")  # claims finalize (queued)
    # Delivery B of batch 2: the guard passes (still running); finalize runs
    # inside B's index_units window, so B's chunk write lands after the rebase.
    orig = ingestor.index_units
    fired = 0

    def interposed(*a, **kw):
        nonlocal fired
        fired += 1
        if fired == 1:
            coord._handle_finalize(job("finalize"), "u")
        return orig(*a, **kw)

    ingestor.index_units = interposed
    try:
        coord._handle_process(job("process", 2, 4, 5), "u")
    finally:
        ingestor.index_units = orig
    assert fired == 1
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc) and doc.status == "ready" and doc.text is not None
    chunks = _chunks(spec, doc_id)
    assert len(chunks) == 5
    assert all(doc.text[c.start : c.end] == c.text for c in chunks)
    staged = spec.get_resource_manager(IndexUnitText).list_resources(
        (QB["doc_id"] == doc_id).build()
    )
    assert list(staged) == []
    run = spec.get_resource_manager(IndexRun).get(doc_id).data
    assert isinstance(run, IndexRun) and run.status == "done"


async def test_a_staged_row_left_by_an_earlier_run_never_reaches_the_next_text():
    # P15: the split step clears staging, so a row a late replay left behind
    # cannot be rejoined into a later run's `SourceDoc.text`.
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=5)
    coord._stage_text(doc_id, 7, "STALE TEXT FROM A PREVIOUS RUN")
    coord.enqueue(doc_id, cid)
    await coord.aclose()
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc) and doc.text is not None
    assert "STALE" not in doc.text


def _fanout_to_the_last_batch(spec, coord, ingestor, cid, doc_id):
    """Split + the three batches by hand (the finalize job is claimed, not run)."""
    from workspace_app.kb.index_jobs import IndexJobPayload

    def job(kind: str, b: int = 0, s: int = 0, e: int = 0) -> IndexJobPayload:
        return IndexJobPayload(
            doc_id=doc_id, collection_id=cid, kind=kind, unit_start=s, unit_end=e, batch_index=b
        )

    coord._handle_split(job("split"), "u", 0, 0)
    coord._handle_process(job("process", 0, 0, 2), "u")
    coord._handle_process(job("process", 1, 2, 4), "u")
    coord._handle_process(job("process", 2, 4, 5), "u")
    return job


def _assert_canonical_everywhere(spec, ingestor, doc_id) -> None:
    from workspace_app.kb.index_cache import IndexCacheStore

    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc) and doc.status == "ready" and doc.text is not None
    chunks = _chunks(spec, doc_id)
    assert len(chunks) == 5
    bad = [(c.seq, c.start, c.end, c.text) for c in chunks if doc.text[c.start : c.end] != c.text]
    assert not bad, bad
    cached = IndexCacheStore(spec).get(ingestor.cache_key(doc_id))
    assert cached is not None
    bad = [(c.seq, c.start, c.end) for c in cached.chunks if doc.text[c.start : c.end] != c.text]
    assert not bad, ("the #390 cache snapshot", bad)
    staged = spec.get_resource_manager(IndexUnitText).list_resources(
        (QB["doc_id"] == doc_id).build()
    )
    assert list(staged) == []
    run = spec.get_resource_manager(IndexRun).get(doc_id).data
    assert isinstance(run, IndexRun) and run.status == "done"


def test_a_duplicate_whose_write_lands_after_the_rebase_changes_no_row():
    """Round 6's forced interleaving, kept as the pin for P17's rule: the
    duplicate's chunk write lands AFTER finalize's rebase while finalize is
    still in flight. Create-only rows mean the write is refused and the
    rebased rows stand; finalize completes with canonical rows everywhere."""
    import threading

    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=5)
    job = _fanout_to_the_last_batch(spec, coord, ingestor, cid, doc_id)

    rebased = threading.Event()  # finalize's real rebase is done
    dup_done = threading.Event()  # the duplicate's tail ran
    orig_rebase = coord._rebase_fanout_offsets
    orig_index_units = ingestor.index_units
    in_finalize = threading.local()

    def rebase(doc, bases, requester):
        orig_rebase(doc, bases, requester)
        if getattr(in_finalize, "yes", False) and not rebased.is_set():
            rebased.set()
            assert dup_done.wait(10), "the duplicate never finished"

    def index_units(*a, **kw):
        assert rebased.wait(10), "finalize never rebased"
        return orig_index_units(*a, **kw)  # the write lands after the rebase

    coord._rebase_fanout_offsets = rebase
    ingestor.index_units = index_units
    errors: list[BaseException] = []

    def finalize():
        in_finalize.yes = True
        try:
            coord._handle_finalize(job("finalize"), "u")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
            rebased.set()

    def duplicate():
        try:
            coord._handle_process(job("process", 2, 4, 5), "u")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
        finally:
            dup_done.set()

    tf, td = threading.Thread(target=finalize), threading.Thread(target=duplicate)
    tf.start()
    td.start()
    td.join(20)
    tf.join(20)
    assert not errors, errors
    _assert_canonical_everywhere(spec, ingestor, doc_id)


def test_a_duplicate_landing_between_the_rebase_and_the_cache_snapshot_changes_no_row():
    """Rounds 6–7: with a duplicate that could overwrite, the #390 snapshot
    caught its batch-relative rows (and, once the duplicate re-snapshotted,
    two blind puts raced). Create-only rows: the duplicate's write is refused,
    the snapshot finalize takes is canonical, and nobody else puts."""
    import threading

    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=5)
    job = _fanout_to_the_last_batch(spec, coord, ingestor, cid, doc_id)

    # The interleaving: finalize rebases → the duplicate's write lands (and is
    # refused) → finalize snapshots the cache → the duplicate finishes.
    rebased = threading.Event()
    written = threading.Event()
    orig_write_cache = ingestor.write_cache
    orig_index_units = ingestor.index_units
    orig_rebase = coord._rebase_fanout_offsets
    in_finalize = threading.local()
    calls: list[str] = []

    def rebase(doc, bases, requester):
        orig_rebase(doc, bases, requester)
        calls.append("rebase:finalize" if getattr(in_finalize, "yes", False) else "rebase:dup")
        rebased.set()

    def index_units(*a, **kw):
        assert rebased.wait(10), "finalize never rebased"
        out = orig_index_units(*a, **kw)
        written.set()
        return out

    def write_cache(doc):
        assert written.wait(10), "the duplicate never wrote"
        orig_write_cache(doc)
        calls.append("cache:finalize" if getattr(in_finalize, "yes", False) else "cache:dup")

    ingestor.write_cache = write_cache
    ingestor.index_units = index_units
    coord._rebase_fanout_offsets = rebase
    errors: list[BaseException] = []

    def finalize():
        in_finalize.yes = True
        try:
            coord._handle_finalize(job("finalize"), "u")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
            rebased.set()

    def duplicate():
        try:
            coord._handle_process(job("process", 2, 4, 5), "u")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
            written.set()

    td, tf = threading.Thread(target=duplicate), threading.Thread(target=finalize)
    td.start()
    tf.start()
    tf.join(20)
    td.join(20)
    assert not errors, errors
    assert calls == ["rebase:finalize", "cache:finalize"]  # the duplicate touched nothing
    _assert_canonical_everywhere(spec, ingestor, doc_id)


def test_fanout_rows_are_create_only_so_a_second_writer_changes_nothing():
    """P17's primitive, directly: `index_units` for a batch whose rows exist
    refuses every row (specstar's create-only), and a batch with SOME rows
    missing (a job redelivered after a crash mid-write) fills only the gaps."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ingestor, _coord = _build(spec, csv_batch=2)
    doc_id = _store_csv(ingestor, cid, rows=5)
    from workspace_app.kb.ingest import chunk_id

    ingestor.index_units(doc_id, (2, 4), seq_base=1_000_000)
    rm = spec.get_resource_manager(DocChunk)
    first = rm.get(chunk_id(doc_id, 1_000_000)).data
    assert isinstance(first, DocChunk)
    # Someone (finalize) moved the row; a second delivery must not move it back.
    rm.patch(chunk_id(doc_id, 1_000_000), MergePatch({"start": 40, "end": 48}))
    ingestor.index_units(doc_id, (2, 4), seq_base=1_000_000)
    again = rm.get(chunk_id(doc_id, 1_000_000)).data
    assert isinstance(again, DocChunk) and (again.start, again.end) == (40, 48)
    # A crashed job that wrote only the first row: the redelivery fills the second.
    rm.permanently_delete(chunk_id(doc_id, 1_000_001))
    ingestor.index_units(doc_id, (2, 4), seq_base=1_000_000)
    rows = sorted(c.seq for c in _chunks(spec, doc_id) if c.seq >= 1_000_000)
    assert rows == [1_000_000, 1_000_001]
    kept = rm.get(chunk_id(doc_id, 1_000_000)).data
    assert isinstance(kept, DocChunk) and (kept.start, kept.end) == (40, 48)
