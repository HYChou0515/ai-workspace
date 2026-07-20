"""IndexCoordinator (#82) — durable indexing queue + background worker.

Indexing moved off the request path onto a specstar job queue (like wiki #59):
enqueue returns immediately; a background consumer runs Ingestor.index in its
own thread, then chains the wiki hook. A bad doc must not wedge the partition.
"""

from __future__ import annotations

import msgspec
from specstar import QB
from specstar.types import Binary, MergePatch, TaskStatus

from workspace_app.kb.index_coordinator import IndexCoordinator
from workspace_app.kb.index_jobs import IndexJob
from workspace_app.resources import Collection, SourceDoc, make_spec


class _FakeIngestor:
    def __init__(self) -> None:
        self.indexed: list[str] = []
        self.cached: list[str] = []
        self.invalidated: list[str] = []

    def index(
        self, doc_id: str, *, source_doc_rm: object | None = None, reraise: bool = False
    ) -> None:
        self.indexed.append(doc_id)

    def write_cache(self, doc_id: str) -> None:
        self.cached.append(doc_id)

    def invalidate_cache(self, doc_id: str) -> None:
        self.invalidated.append(doc_id)


class _FakeWiki:
    def __init__(self) -> None:
        self.hooked: list[str] = []

    async def on_doc_indexed(self, doc_id: str, *, requested_by: str | None = None) -> None:
        self.hooked.append(doc_id)


def _collection(spec) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id


def _doc(spec, cid: str, path: str = "a.md") -> str:
    rm = spec.get_resource_manager(SourceDoc)
    rev = rm.create(
        SourceDoc(collection_id=cid, path=path, content=Binary(data=b"x"), status="indexing")
    )
    return rev.resource_id


async def test_enqueued_doc_is_indexed_then_handed_to_the_wiki():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    ing, wiki = _FakeIngestor(), _FakeWiki()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=wiki)  # ty: ignore[invalid-argument-type]

    coord.enqueue(doc_id, cid)  # producer returns immediately
    await coord.aclose()  # drain the background consumer

    assert ing.indexed == [doc_id]  # the worker indexed it (off the request path)
    assert wiki.hooked == [doc_id]  # …then chained the index → wiki hook


async def test_successful_index_writes_the_result_cache():
    # #390: after a real index completes, the coordinator snapshots the result
    # into the cross-path cache so a later move / re-upload reuses it.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing)  # ty: ignore[invalid-argument-type]

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert ing.indexed == [doc_id]
    assert ing.cached == [doc_id]  # the result was cached after indexing


async def test_cache_write_failure_does_not_fail_the_index_job():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)

    class _CacheBoom(_FakeIngestor):
        def write_cache(self, doc_id: str) -> None:
            raise RuntimeError("cache backend down")

    wiki = _FakeWiki()
    coord = IndexCoordinator(spec, _CacheBoom(), wiki_coordinator=wiki)  # ty: ignore[invalid-argument-type]
    coord.enqueue(doc_id, cid)
    await coord.aclose()

    # The cache-write ran BEFORE the wiki hook; its blip was swallowed, so the
    # run still completed downstream — best-effort, never fails the index job.
    assert wiki.hooked == [doc_id]


class _FakeQuality:
    def __init__(self) -> None:
        self.scored: list[tuple[str, str]] = []

    def score_doc(self, doc_id: str, acting_user: str) -> None:
        self.scored.append((doc_id, acting_user))


async def test_enqueued_doc_is_scored_after_indexing():
    # #105: once a doc reaches "ready", the index worker hands it to the quality
    # coordinator (off the request path), crediting the doc's owner.
    spec = make_spec(default_user="owner")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    ing, quality = _FakeIngestor(), _FakeQuality()
    coord = IndexCoordinator(spec, ing, quality_coordinator=quality)  # ty: ignore[invalid-argument-type]

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert ing.indexed == [doc_id]
    assert quality.scored == [(doc_id, "owner")]  # scored as the doc's owner


async def test_quality_hook_failure_does_not_fail_the_index_job():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)

    class _BoomQuality:
        def score_doc(self, doc_id: str, acting_user: str) -> None:
            raise RuntimeError("ollama is down")

    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing, quality_coordinator=_BoomQuality())  # ty: ignore
    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert ing.indexed == [doc_id]  # indexing succeeded despite the judge blowing up


class _FakeCardGen:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, list[str], str | None]] = []

    def enqueue(
        self, collection_id: str, doc_ids: list[str], *, requested_by: str | None = None
    ) -> str:
        self.enqueued.append((collection_id, list(doc_ids), requested_by))
        return "job-id"


def _collection_auto_digest(spec, enabled: bool) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", auto_digest=enabled))
        .resource_id
    )


async def test_digest_runs_after_indexing_when_the_collection_opts_in():
    # #377: a collection with auto_digest on hands each ready doc to the digest
    # (the SAME card-drafting pass that raises clarification questions), off the
    # request path, crediting the doc's owner.
    spec = make_spec(default_user="owner")
    cid = _collection_auto_digest(spec, enabled=True)
    doc_id = _doc(spec, cid)
    ing, cg = _FakeIngestor(), _FakeCardGen()
    coord = IndexCoordinator(spec, ing, card_gen_coordinator=cg)  # ty: ignore[invalid-argument-type]

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert ing.indexed == [doc_id]
    assert cg.enqueued == [(cid, [doc_id], "owner")]  # digested as the doc's owner


async def test_digest_is_skipped_when_the_collection_opts_out():
    # Default (auto_digest off) → the digest never auto-runs; it's a manual action.
    spec = make_spec(default_user="owner")
    cid = _collection_auto_digest(spec, enabled=False)
    doc_id = _doc(spec, cid)
    ing, cg = _FakeIngestor(), _FakeCardGen()
    coord = IndexCoordinator(spec, ing, card_gen_coordinator=cg)  # ty: ignore[invalid-argument-type]

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert ing.indexed == [doc_id]
    assert cg.enqueued == []


async def test_digest_hook_failure_does_not_fail_the_index_job():
    spec = make_spec(default_user="u")
    cid = _collection_auto_digest(spec, enabled=True)
    doc_id = _doc(spec, cid)

    class _BoomCardGen:
        def enqueue(self, collection_id, doc_ids, *, requested_by=None):
            raise RuntimeError("queue is down")

    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing, card_gen_coordinator=_BoomCardGen())  # ty: ignore
    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert ing.indexed == [doc_id]  # indexing succeeded despite the digest enqueue blowing up


async def test_indexing_without_a_wiki_coordinator_still_works():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)  # ty: ignore[invalid-argument-type]

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert ing.indexed == [doc_id]


async def test_wiki_hook_failure_does_not_fail_the_index_job():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)

    class _BoomWiki:
        async def on_doc_indexed(self, doc_id: str) -> None:
            raise RuntimeError("wiki maintainer exploded")

    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=_BoomWiki())  # ty: ignore
    coord.enqueue(doc_id, cid)
    await coord.aclose()

    assert ing.indexed == [doc_id]  # indexing succeeded despite the wiki hook blowing up


async def test_aclose_is_a_noop_when_idle():
    spec = make_spec(default_user="u")
    # never enqueued, never consuming → aclose returns without spinning a thread
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore
    await coord.aclose()


async def test_start_consuming_is_idempotent_and_accepts_a_factory():
    from specstar.message_queue import SimpleMessageQueueFactory

    spec = make_spec(default_user="u")
    coord = IndexCoordinator(
        spec,
        _FakeIngestor(),  # ty: ignore[invalid-argument-type]
        wiki_coordinator=None,
        message_queue_factory=SimpleMessageQueueFactory(),
    )
    coord.start_consuming()
    coord.start_consuming()  # second call is a no-op (already consuming)
    await coord.aclose()


async def test_enqueued_jobs_are_partitioned_per_doc_so_workers_parallelize():
    """Index jobs are partitioned by DOC id (#134), so specstar serializes only
    same-doc peers — two docs in ONE collection carry DIFFERENT keys and any
    worker may claim either. Embedder load is bounded by the number of worker
    pods (k8s replicas), not by per-collection serialization."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1, d2 = _doc(spec, cid, "a.md"), _doc(spec, cid, "b.md")
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    coord.enqueue(d1, cid)
    coord.enqueue(d2, cid)

    rm = spec.get_resource_manager(IndexJob)
    jobs = list(rm.list_resources(QB["status"].eq(TaskStatus.PENDING).build()))
    assert {j.data.payload.doc_id for j in jobs} == {d1, d2}  # ty: ignore[unresolved-attribute]
    # collection_id is still on the payload (wiki hook + observability need it)…
    assert all(j.data.payload.collection_id == cid for j in jobs)  # ty: ignore
    # …but the partition key is the DOC id, so different docs never block each
    # other while the same doc is serialized against concurrent re-indexing.
    assert {j.data.partition_key for j in jobs} == {d1, d2}  # ty: ignore[unresolved-attribute]


async def test_a_failing_doc_does_not_wedge_the_queue():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1, d2 = _doc(spec, cid, "bad.md"), _doc(spec, cid, "ok.md")

    class _BoomOnFirst:
        def __init__(self) -> None:
            self.indexed: list[str] = []

        def index(
            self, doc_id: str, *, source_doc_rm: object | None = None, reraise: bool = False
        ) -> None:
            self.indexed.append(doc_id)
            if doc_id == d1:
                raise RuntimeError("embed crashed")

        def write_cache(self, doc_id: str) -> None:
            pass

    ing = _BoomOnFirst()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)  # ty: ignore[invalid-argument-type]
    coord.enqueue(d1, cid)
    coord.enqueue(d2, cid)
    await coord.aclose()

    # the bad doc was attempted AND the next one still ran — the partition drained.
    assert d1 in ing.indexed
    assert d2 in ing.indexed


# ── #249: single-job transient retry vs permanent error ───────────────


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _FlakyEmbedder:
    """An embedder whose first ``fail_times`` calls raise ``error``."""

    def __init__(self, *, fail_times: int, error: Exception) -> None:
        from workspace_app.resources.kb import EMBED_DIM

        self._dim = EMBED_DIM
        self._fail_times = fail_times
        self._error = error
        self.calls = 0

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def identity(self) -> str:
        return f"flaky-{self._dim}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error
        return [[0.0] * self._dim for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self._dim


def _real_coord(spec, embedder, **kw):
    from workspace_app.kb.chunker import FixedTokenChunker
    from workspace_app.kb.ingest import Ingestor

    ing = Ingestor(
        spec, chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=2), embedder=embedder
    )
    return ing, IndexCoordinator(spec, ing, wiki_coordinator=None, **kw)


async def test_transient_failure_retries_and_recovers_to_ready():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = _FlakyEmbedder(fail_times=1, error=_StatusError(503))  # blips once, then works
    ing, coord = _real_coord(spec, emb, job_max_retries=3)
    doc_id = ing.store(collection_id=cid, user="u", filename="a.md", data=b"hello world")[0]

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    got = spec.get_resource_manager(SourceDoc).get(doc_id)
    assert got.data.status == "ready"  # the re-delivered job succeeded
    assert emb.calls == 2  # one blip, one recovery — no permanent error


async def test_transient_failure_shows_retrying_then_errors_when_exhausted():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = _FlakyEmbedder(fail_times=99, error=_StatusError(502))  # never recovers
    ing, coord = _real_coord(spec, emb, job_max_retries=2)
    doc_id = ing.store(collection_id=cid, user="u", filename="a.md", data=b"hello world")[0]

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    got = spec.get_resource_manager(SourceDoc).get(doc_id)
    assert got.data.status == "error"  # only after exhausting the retries
    assert emb.calls == 3  # delivered max_retries + 1 times (retries 0,1,2)


async def test_permanent_failure_errors_at_once_without_retrying():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = _FlakyEmbedder(fail_times=99, error=_StatusError(400))  # a bad request never recovers
    ing, coord = _real_coord(spec, emb, job_max_retries=3)
    doc_id = ing.store(collection_id=cid, user="u", filename="a.md", data=b"hello world")[0]

    coord.enqueue(doc_id, cid)
    await coord.aclose()

    got = spec.get_resource_manager(SourceDoc).get(doc_id)
    assert got.data.status == "error"
    assert "400" in got.data.status_detail  # the cause is surfaced
    assert emb.calls == 1  # no retry on a permanent error


async def test_set_doc_status_is_a_noop_when_the_doc_was_deleted():
    """A doc cascaded away between a failed attempt and the status write must not
    crash the worker (#249, mirrors _last_updater's deleted-doc guard)."""
    spec = make_spec(default_user="u")
    _, coord = _real_coord(spec, _FlakyEmbedder(fail_times=0, error=_StatusError(500)))
    coord._set_doc_status("gone", "u", status="error", detail="x")  # noqa: SLF001 — no raise


async def test_indexing_in_a_worker_preserves_the_docs_last_updater(monkeypatch):
    """#83: the index consumer runs in a job pod with NO request user, so the
    spec's acting user is the bare default. An unguarded index() would stamp the
    SourceDoc's updated_by with that default, erasing the real uploader. The
    coordinator must preserve the doc's last real updater across the worker run.
    """
    from workspace_app.kb.chunker import FixedTokenChunker
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.kb.ingest import Ingestor
    from workspace_app.resources.kb import EMBED_DIM

    who = {"u": "alice"}
    spec = make_spec(default_user=lambda: who["u"])
    cid = _collection(spec)
    ing = Ingestor(
        spec,
        chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=2),
        embedder=HashEmbedder(dim=EMBED_DIM),
    )
    # alice uploads → the SourceDoc is created + stamped as alice (request path).
    doc_id = ing.store(collection_id=cid, user="alice", filename="a.md", data=b"hello world")[0]
    drm = spec.get_resource_manager(SourceDoc)
    assert drm.get(doc_id).info.updated_by == "alice"

    # The job pod has no request user — the consumer runs as the bare default.
    who["u"] = "index-worker"
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)
    coord.enqueue(doc_id, cid)
    await coord.aclose()

    got = drm.get(doc_id)
    assert got.data.status == "ready"  # the worker really indexed it
    assert got.info.updated_by == "alice"  # …but updated_by stays the uploader, not the worker


async def test_chunks_are_credited_to_the_requester_not_the_doc_owner():
    """#186: bob reindexes alice's doc. The worker runs in a job pod with NO
    request user. The SourceDoc stays alice's (its content was not re-authored,
    #83), but the DocChunks the run regenerates are derived artifacts credited to
    the *requester* who triggered the run — read off the index job's created_by,
    NOT the bare worker default."""
    from workspace_app.kb.chunker import FixedTokenChunker
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.kb.ingest import Ingestor
    from workspace_app.resources import DocChunk
    from workspace_app.resources.kb import EMBED_DIM

    who = {"u": "alice"}
    spec = make_spec(default_user=lambda: who["u"])
    cid = _collection(spec)
    ing = Ingestor(
        spec,
        chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=2),
        embedder=HashEmbedder(dim=EMBED_DIM),
    )
    # alice uploads → the SourceDoc is stamped as alice (request path).
    doc_id = ing.store(collection_id=cid, user="alice", filename="a.md", data=b"hello world")[0]
    # bob clicks reindex — the producer runs in bob's request, so the IndexJob's
    # created_by is bob (the requester), captured via get_user_id at enqueue.
    who["u"] = "bob"
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)
    coord.enqueue(doc_id, cid)
    # The job pod has no request user — the consumer runs as the bare default.
    who["u"] = "index-worker"
    await coord.aclose()

    drm = spec.get_resource_manager(SourceDoc)
    assert drm.get(doc_id).info.updated_by == "alice"  # content owner unchanged (#83)
    chrm = spec.get_resource_manager(DocChunk)
    chunks = chrm.list_resources((QB["source_doc_id"] == doc_id).build())
    assert chunks  # the worker really wrote chunks
    # #186: every chunk this run wrote is credited to the requester, not the worker.
    assert all(c.info.created_by == "bob" for c in chunks)  # ty: ignore[unresolved-attribute]
    assert all(c.info.updated_by == "bob" for c in chunks)  # ty: ignore[unresolved-attribute]


async def test_doc_deleted_before_the_index_job_runs_is_a_graceful_noop():
    """#83: reading the doc's updater up front means a doc deleted between
    enqueue and run is a no-op (it's gone — nothing to index), not a crash."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)  # ty: ignore[invalid-argument-type]

    coord.enqueue(doc_id, cid)
    # The consumer starts on aclose, so delete first → it runs with the doc gone.
    spec.get_resource_manager(SourceDoc).permanently_delete(doc_id)
    await coord.aclose()

    assert ing.indexed == []  # skipped, not indexed, no crash


# ── reindex-on-edit trigger (#87 P2) ──────────────────────────────────────
# A user edits a KB doc through specstar's auto-CRUD: upload a new content blob
# then CAS-PATCH /source-doc/{id}. `install_reindex_on_edit` wires a SourceDoc
# `on_success(patch)` handler so that edit auto-enqueues a reindex — no custom
# edit endpoint. Scoped to `patch` (not `update`) so the worker's own
# `rm.update(status=...)` writes can't re-fire it (no loop).


def _pending_jobs(spec) -> list[str]:
    rm = spec.get_resource_manager(IndexJob)
    return [
        j.data.payload.doc_id
        for j in rm.list_resources(QB["status"].eq(TaskStatus.PENDING).build())
    ]


def test_patching_a_doc_enqueues_a_reindex():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore
    coord.install_reindex_on_edit()

    # The FE edit lands as an RFC 7386 merge-patch on the doc's content.
    spec.get_resource_manager(SourceDoc).patch(doc_id, MergePatch({"text": "edited"}))

    assert _pending_jobs(spec) == [doc_id]  # exactly one reindex queued


def test_an_internal_update_does_not_trigger_a_reindex():
    """The index worker ends with `rm.update(status="ready")`; the reindex route
    does `rm.update(status="indexing")`. Those are `update`, not `patch`, so the
    trigger (scoped to patch) must NOT fire on them — otherwise reindex loops."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore
    coord.install_reindex_on_edit()

    rm = spec.get_resource_manager(SourceDoc)
    doc = rm.get(doc_id).data
    rm.update(doc_id, msgspec.structs.replace(doc, status="ready"))

    assert _pending_jobs(spec) == []  # an update never enqueues a reindex


def test_reindex_trigger_swallows_enqueue_errors():
    """`on_success` runs in the PATCH request stack; a raise there misclassifies
    the committed patch as a failure → HTTP 500. The handler must swallow so the
    user's edit still succeeds even if the queue is momentarily unavailable."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore
    coord.install_reindex_on_edit()

    def boom(*_a, **_k) -> None:
        raise RuntimeError("queue down")

    coord.enqueue = boom  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]

    # The patch itself must still succeed (no exception bubbles to the caller).
    info = spec.get_resource_manager(SourceDoc).patch(doc_id, MergePatch({"text": "x"}))
    assert info is not None
    assert spec.get_resource_manager(SourceDoc).get(doc_id).data.text == "x"


# ── reindex coalescing (#134) ─────────────────────────────────────────────
# Pressing reindex N times used to flip status→"indexing" and enqueue N full
# re-index jobs; the doc then sat at "indexing" until every redundant job
# drained (each one re-chunks + re-embeds + re-triggers the wiki), so it looked
# permanently stuck. `enqueue` now coalesces: while a reindex for a doc is
# already PENDING, repeat enqueues for that SAME doc are no-ops.


def test_reindex_is_coalesced_while_a_job_is_pending():
    """#134: mashing reindex must not pile up N jobs. A second enqueue for a doc
    that already has a PENDING job is a no-op — the queued job will index the
    latest content when it runs."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    assert coord.enqueue(doc_id, cid) is True  # first click queues a job
    assert coord.enqueue(doc_id, cid) is False  # mash → coalesced onto the pending one
    assert coord.enqueue(doc_id, cid) is False

    assert _pending_jobs(spec) == [doc_id]  # exactly ONE pending job, not three


def test_enqueue_does_not_coalesce_across_different_docs():
    """Coalescing is per-doc: a different doc's reindex always gets its own job
    (unpartitioned jobs still parallelize across workers)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1, d2 = _doc(spec, cid, "a.md"), _doc(spec, cid, "b.md")
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    assert coord.enqueue(d1, cid) is True
    assert coord.enqueue(d2, cid) is True  # different doc → its own job, not coalesced
    assert sorted(_pending_jobs(spec)) == sorted([d1, d2])


def test_a_processing_job_does_not_block_a_fresh_reindex():
    """Only PENDING jobs coalesce. A job already PROCESSING may have read stale
    content before an edit landed, so a reindex arriving mid-flight still needs
    its own rerun — otherwise the edit would never be indexed."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    assert coord.enqueue(doc_id, cid) is True
    # Flip the queued job to PROCESSING, as the consumer does when it claims it
    # (credited to the job's creator — the manager carries no default user, #186).
    jrm = spec.get_resource_manager(IndexJob)
    job = next(iter(jrm.list_resources(QB["status"].eq(TaskStatus.PENDING).build())))
    data = job.data
    assert isinstance(data, IndexJob)
    with jrm.using(user=job.info.created_by):  # ty: ignore[unresolved-attribute]
        jrm.update(
            job.info.resource_id,  # ty: ignore[unresolved-attribute]
            msgspec.structs.replace(data, status=TaskStatus.PROCESSING),
        )

    # A fresh reindex must still enqueue (it can't ride on the in-flight job).
    assert coord.enqueue(doc_id, cid) is True
    assert _pending_jobs(spec) == [doc_id]


# ── whole-collection reindex as a JOB (#569) ──────────────────────────────
# "Re-read all" used to do the whole fan-out INSIDE the HTTP request: it loaded
# every SourceDoc (blob + extracted text) into memory, flipped each status and
# enqueued each doc — synchronously, on the event loop, with no `await`. At a
# thousand docs that froze the entire API pod for minutes and could not be
# aborted (no await point ⇒ no client-disconnect detection). The fan-out is now
# its own job kind: the request leaves ONE row behind and returns; a worker
# claiming that row sends every doc in the collection through.


def _pending_kinds(spec) -> list[str]:
    rm = spec.get_resource_manager(IndexJob)
    return [
        j.data.payload.kind for j in rm.list_resources(QB["status"].eq(TaskStatus.PENDING).build())
    ]


def test_enqueue_collection_leaves_exactly_one_job_and_touches_no_doc():
    """The producer's whole job is to leave a marker behind. It must NOT walk the
    collection — that walk is what used to block the request path."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1, d2 = _doc(spec, cid, "a.md"), _doc(spec, cid, "b.md")
    rm = spec.get_resource_manager(SourceDoc)
    for d in (d1, d2):  # both healthy before the button is pressed
        rm.update(d, msgspec.structs.replace(rm.get(d).data, status="ready"))
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    assert coord.enqueue_collection(cid) is True

    assert _pending_kinds(spec) == ["collection"]  # ONE job, not one per doc
    # No doc was read or written by the producer — the worker owns all of that.
    assert [rm.get(d).data.status for d in (d1, d2)] == ["ready", "ready"]


async def test_the_collection_job_sends_every_doc_in_the_collection_through():
    """A worker claiming the `collection` job re-indexes every doc it holds."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1, d2 = _doc(spec, cid, "a.md"), _doc(spec, cid, "b.md")
    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)  # ty: ignore

    coord.enqueue_collection(cid)
    await coord.aclose()  # drain: collection job → per-doc jobs → indexed

    assert sorted(ing.indexed) == sorted([d1, d2])


async def test_the_collection_job_ignores_docs_of_other_collections():
    spec = make_spec(default_user="u")
    mine, theirs = _collection(spec), _collection(spec)
    d1 = _doc(spec, mine, "a.md")
    _doc(spec, theirs, "b.md")
    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)  # ty: ignore

    coord.enqueue_collection(mine)
    await coord.aclose()

    assert ing.indexed == [d1]


async def test_the_collection_job_invalidates_each_docs_cache_before_requeueing():
    """#390: re-read means FORCE recompute, so the cached result is dropped first.
    That drop moved into the worker with the rest of the walk."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1, d2 = _doc(spec, cid, "a.md"), _doc(spec, cid, "b.md")
    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)  # ty: ignore

    coord.enqueue_collection(cid)
    await coord.aclose()

    assert sorted(ing.invalidated) == sorted([d1, d2])


async def test_only_failed_sends_just_the_errored_docs_through():
    """#223 survives the move to a job: `only="failed"` rides the payload, and the
    WORKER (not the route) applies it."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    good, bad = _doc(spec, cid, "good.md"), _doc(spec, cid, "bad.md")
    rm = spec.get_resource_manager(SourceDoc)
    rm.update(good, msgspec.structs.replace(rm.get(good).data, status="ready"))
    rm.update(bad, msgspec.structs.replace(rm.get(bad).data, status="error"))
    ing = _FakeIngestor()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)  # ty: ignore

    coord.enqueue_collection(cid, only="failed")
    await coord.aclose()

    assert ing.indexed == [bad]


def test_pressing_re_read_all_again_coalesces_onto_the_pending_run():
    """The anti-mash guard the per-doc path already has (#134), at collection
    scope: a second press while one is still queued is a no-op, and says so —
    that `False` is what the UI turns into 'already running' instead of a second
    'sent' confirmation."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _doc(spec, cid, "a.md")
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    assert coord.enqueue_collection(cid) is True
    assert coord.enqueue_collection(cid) is False
    assert coord.enqueue_collection(cid) is False

    assert _pending_kinds(spec) == ["collection"]


def test_two_collections_do_not_coalesce_onto_each_other():
    spec = make_spec(default_user="u")
    c1, c2 = _collection(spec), _collection(spec)
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    assert coord.enqueue_collection(c1) is True
    assert coord.enqueue_collection(c2) is True
    assert _pending_kinds(spec) == ["collection", "collection"]


def test_a_pending_doc_job_never_blocks_a_whole_collection_re_read():
    """The collection job's partition key lives in its OWN namespace, so it can
    never be mistaken for a doc's key. A doc id is a slash-FREE token (kb.doc_id),
    so a `/`-bearing key is unforgeable by any doc — without that, a doc whose id
    happened to match would silently swallow the collection-wide re-read."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid, "a.md")
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    assert coord.enqueue(doc_id, cid) is True  # a single doc is already queued
    assert coord.enqueue_collection(cid) is True  # …the collection re-read still queues
    assert sorted(_pending_kinds(spec)) == ["collection", "split"]


def test_a_pending_failed_only_recovery_does_not_swallow_a_full_re_read():
    """The failed-only run covers a strict SUBSET of the collection, so it must
    not coalesce a whole-collection re-read onto itself. It used to: the key was
    the collection alone, so clicking "Retry failed" (3 docs) and then "Re-read
    all" (1000 docs) dropped the second request AND told the user it was already
    running — the worst combination, a silent loss reported as success."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _doc(spec, cid, "a.md")
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    assert coord.enqueue_collection(cid, only="failed") is True
    assert coord.enqueue_collection(cid) is True  # the bigger run still gets queued

    assert _pending_kinds(spec) == ["collection", "collection"]


def test_each_only_variant_still_coalesces_onto_itself():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _doc(spec, cid, "a.md")
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    assert coord.enqueue_collection(cid, only="failed") is True
    assert coord.enqueue_collection(cid, only="failed") is False
    assert coord.enqueue_collection(cid) is True
    assert coord.enqueue_collection(cid) is False


async def test_a_doc_is_never_left_indexing_with_no_job_behind_it():
    """Per-doc errors are swallowed so one bad doc can't strand the rest — which
    makes the ORDER load-bearing. Flipping to `indexing` before the enqueue meant
    a failure in between left the doc `indexing` for ever with nothing queued:
    permanently stuck, and invisible (the user was already told "sent")."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid, "a.md")
    rm = spec.get_resource_manager(SourceDoc)
    rm.update(doc_id, msgspec.structs.replace(rm.get(doc_id).data, status="ready"))

    class _EnqueueBoom(_FakeIngestor):
        def invalidate_cache(self, doc_id: str) -> None:
            raise RuntimeError("cache backend down")

    coord = IndexCoordinator(spec, _EnqueueBoom(), wiki_coordinator=None)  # ty: ignore
    coord.enqueue_collection(cid)
    await coord.aclose()

    # The doc was not re-read — but it says so, instead of claiming to be busy.
    assert rm.get(doc_id).data.status == "ready"


async def test_an_empty_requester_is_not_replaced_by_the_worker_default():
    """`requested_by=""` is a real user id in a no-auth deploy. A truthiness
    check silently swapped it for the worker's own default — the exact
    miscrediting the parameter exists to prevent (#186)."""
    spec = make_spec(default_user="worker-default")
    cid = _collection(spec)
    doc_id = _doc(spec, cid, "a.md")
    coord = IndexCoordinator(spec, _FakeIngestor(), wiki_coordinator=None)  # ty: ignore

    coord.enqueue(doc_id, cid, requested_by="")

    jrm = spec.get_resource_manager(IndexJob)
    job = next(iter(jrm.list_resources(QB["status"].eq(TaskStatus.PENDING).build())))
    assert job.info.created_by == ""  # ty: ignore[unresolved-attribute]
