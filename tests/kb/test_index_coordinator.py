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

    def index(self, doc_id: str, *, source_doc_rm: object | None = None) -> None:
        self.indexed.append(doc_id)


class _FakeWiki:
    def __init__(self) -> None:
        self.hooked: list[str] = []

    async def on_doc_indexed(self, doc_id: str) -> None:
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


async def test_enqueued_jobs_are_unpartitioned_so_workers_parallelize():
    """Index jobs carry NO partition_key, so any worker may claim any pending
    job (specstar blocks only same-key peers). Two docs in ONE collection are
    therefore independent — embedder load is bounded by the number of worker
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
    # …but it is NOT the partition key, so the two jobs never block each other.
    assert all(j.data.partition_key is None for j in jobs)  # ty: ignore[unresolved-attribute]


async def test_a_failing_doc_does_not_wedge_the_queue():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    d1, d2 = _doc(spec, cid, "bad.md"), _doc(spec, cid, "ok.md")

    class _BoomOnFirst:
        def __init__(self) -> None:
            self.indexed: list[str] = []

        def index(self, doc_id: str, *, source_doc_rm: object | None = None) -> None:
            self.indexed.append(doc_id)
            if doc_id == d1:
                raise RuntimeError("embed crashed")

    ing = _BoomOnFirst()
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None)  # ty: ignore[invalid-argument-type]
    coord.enqueue(d1, cid)
    coord.enqueue(d2, cid)
    await coord.aclose()

    # the bad doc was attempted AND the next one still ran — the partition drained.
    assert d1 in ing.indexed
    assert d2 in ing.indexed


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
