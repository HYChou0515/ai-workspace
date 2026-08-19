"""#715: importing a large archive must not hold the HTTP request open.

The synchronous path writes every document before it answers, which is right for
restoring a backup you exported yourself — a person clicks it and waits a few
seconds. It is wrong for a machine pushing a prepared knowledge base: a 207 MB
archive spent ten minutes in it and came back 504. These cover the asynchronous
contract that replaces it for that use: stage the archive, return a run id, do
the writing on a worker.
"""

from __future__ import annotations

import io
import json
import threading
import zipfile

import pytest
from specstar import QB, SpecStar
from specstar.types import PreconditionFailedError

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.import_jobs import ImportCoordinator, ImportPayload, ImportRun
from workspace_app.kb.index_coordinator import IndexCoordinator
from workspace_app.kb.index_jobs import IndexJob
from workspace_app.kb.ingest import Ingestor
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection, ContextCard, SourceDoc

MANIFEST_PATH = ".kb-collection/manifest.json"


def _archive(members: dict[str, bytes], cards: list[dict] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in members.items():
            zf.writestr(path, data)
        zf.writestr(
            MANIFEST_PATH,
            json.dumps(
                {"version": 1, "collection": {"name": "archived"}, "context_cards": cards or []},
                ensure_ascii=False,
            ),
        )
    return buf.getvalue()


def _coordinator(spec: SpecStar) -> ImportCoordinator:
    """The real IndexCoordinator, never consumed.

    A hand-written stub here would only ever prove "we called enqueue", which
    survives any change to what enqueueing actually requires. The real one leaves
    IndexJob rows behind, so the assertion is about the queue that exists rather
    than about our own call."""
    ing = Ingestor(
        spec, chunker=FixedTokenChunker(max_tokens=64), embedder=HashEmbedder(dim=EMBED_DIM)
    )
    return ImportCoordinator(spec, ingestor=ing, index_coordinator=IndexCoordinator(spec, ing))


def _index_jobs(spec: SpecStar) -> int:
    return spec.get_resource_manager(IndexJob).count_resources()


def _collection(spec: SpecStar, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _docs(spec: SpecStar, cid: str) -> list[SourceDoc]:
    rm = spec.get_resource_manager(SourceDoc)
    out = []
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        assert isinstance(r.data, SourceDoc)
        out.append(r.data)
    return out


def _cards(spec: SpecStar, cid: str) -> list[ContextCard]:
    rm = spec.get_resource_manager(ContextCard)
    out = []
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        assert isinstance(r.data, ContextCard)
        out.append(r.data)
    return out


def _run_of(spec: SpecStar, run_id: str) -> ImportRun:
    data = spec.get_resource_manager(ImportRun).get(run_id).data
    assert isinstance(data, ImportRun)
    return data


def _enforce_etags(monkeypatch, rm) -> None:
    """Make ``modify(expected_etag=…)`` behave the way a real backend promises.

    The in-memory backend does NOT: two writers holding the same etag both succeed
    (probed directly — zero conflicts in twenty rounds). Every compare-and-swap in
    this codebase is therefore untestable against it, and a race test written
    against it proves nothing and fails at random. This double models the other
    side's contract — compare-and-set under a lock — which is what the code is
    written against.
    """
    real_modify = rm.modify
    real_get = rm.get
    guard = threading.Lock()
    latest: dict[str, object] = {}

    def checked_modify(resource_id, data, **kw):
        expected = kw.pop("expected_etag", None)
        with guard:
            current = latest.get(resource_id, real_get(resource_id).info.etag)
            if expected is not None and expected != current:
                raise PreconditionFailedError(resource_id, str(expected), str(current))
            out = real_modify(resource_id, data, **kw)
            latest[resource_id] = real_get(resource_id).info.etag
            return out

    monkeypatch.setattr(rm, "modify", checked_modify)


async def test_enqueue_returns_before_any_document_is_written():
    """The whole point: the caller gets an id back while the archive is still only
    staged. If enqueue wrote the documents itself we would have moved the
    ten-minute wait rather than removed it."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)

    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"alpha", "b.md": b"beta"}),
        mode="overwrite",
        user="u",
    )

    assert run_id
    assert _docs(spec, cid) == []  # nothing written yet — that is the contract
    assert _run_of(spec, run_id).collection_id == cid


async def test_draining_the_queue_lands_the_documents_and_the_cards():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    zip_data = _archive(
        {"a.md": b"alpha", "b.md": b"beta"},
        cards=[{"keys": ["M4"], "title": "M4", "body": "edge", "reference_paths": ["a.md"]}],
    )

    coord.enqueue(collection_id=cid, zip_data=zip_data, mode="overwrite", user="u")
    await coord.aclose()

    assert sorted(d.path for d in _docs(spec, cid)) == ["a.md", "b.md"]
    assert [c.keys for c in _cards(spec, cid)] == [["M4"]]
    assert _index_jobs(spec) == 2  # each restored document is queued for indexing


async def test_the_run_reports_what_landed_and_what_did_not():
    """A caller who cannot watch the request needs the outcome per document, not
    just "finished" — a half-applied import has to be visible as one."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)

    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"alpha", "b.md": b"beta"}),
        mode="overwrite",
        user="u",
    )
    await coord.aclose()

    run = _run_of(spec, run_id)
    assert run.members == 2  # documents the archive held
    assert run.written == 2  # documents that landed
    assert run.errors == []
    assert run.finished is True


async def test_the_staged_archive_is_released_once_the_import_finishes():
    """A 200 MB blob per import is not something to keep after it is unpacked. It
    is held only while a retry could still need it."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)

    run_id = coord.enqueue(
        collection_id=cid, zip_data=_archive({"a.md": b"alpha"}), mode="overwrite", user="u"
    )
    await coord.aclose()

    assert _run_of(spec, run_id).archive is None


@pytest.mark.parametrize("mode", ["overwrite", "skip"])
async def test_mode_reaches_the_worker(mode: str):
    """`mode` is chosen by the caller at enqueue time but applied by the worker, so
    it has to survive the trip on the run rather than living in the request."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    spec.get_resource_manager(ContextCard).create(
        ContextCard(collection_id=cid, keys=["M4"], norm_keys=["m4"], title="kept", body="orig")
    )
    coord = _coordinator(spec)

    coord.enqueue(
        collection_id=cid,
        zip_data=_archive({}, cards=[{"keys": ["M4"], "title": "new", "body": "new"}]),
        mode=mode,
        user="u",
    )
    await coord.aclose()

    (card,) = _cards(spec, cid)
    assert card.body == ("new" if mode == "overwrite" else "orig")


async def test_one_unreadable_document_does_not_take_its_batch_down_with_it(monkeypatch):
    """A batch is a scheduling unit, not a transaction. If one member fails, the
    rest of its batch must still land and the run must name the member that did
    not — "batch 3 failed" is not something anyone can act on."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    real = coord._ingestor.store_file

    def flaky(*, collection_id, user, path, data):
        if path == "b.md":
            raise OSError("disk said no")
        return real(collection_id=collection_id, user=user, path=path, data=data)

    monkeypatch.setattr(coord._ingestor, "store_file", flaky)

    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"alpha", "b.md": b"beta", "c.md": b"gamma"}),
        mode="overwrite",
        user="u",
    )
    await coord.aclose()

    run = _run_of(spec, run_id)
    assert sorted(d.path for d in _docs(spec, cid)) == ["a.md", "c.md"]
    assert run.written == 2
    assert len(run.errors) == 1
    assert run.errors[0].startswith("b.md: OSError")
    assert run.finished is True  # a partial import still finishes — and says so


@pytest.mark.parametrize("mode", ["overwrite", "skip"])
async def test_mode_applies_to_documents_too_not_only_cards(mode: str):
    """`mode` decides a DOCUMENT collision as well as a card one.

    The synchronous path skips a colliding path outright; the asynchronous one wrote
    unconditionally, so `skip` quietly replaced files it had promised to leave alone.
    The earlier mode test parametrised both values but asserted only on cards, so it
    stayed green over that.
    """
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    coord._ingestor.store_file(collection_id=cid, user="u", path="a.md", data=b"ORIGINAL")

    coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"REPLACED"}),
        mode=mode,
        user="u",
    )
    await coord.aclose()

    rm = spec.get_resource_manager(SourceDoc)
    (doc,) = [d for d in _docs(spec, cid) if d.path == "a.md"]
    body = rm.restore_binary(doc).content.data
    assert body == (b"REPLACED" if mode == "overwrite" else b"ORIGINAL")


async def test_concurrent_batches_do_not_lose_join_slots(monkeypatch):
    """`process` jobs carry `partition_key=None` so they parallelise on purpose. A
    plain read-modify-write on the run then loses slots, and a lost slot is not a
    cosmetic miscount: `_all_accounted` never holds, so finalize never fires, the
    cards are never restored, the staged archive is never released, and the caller
    polls `queued` forever over an import whose documents all landed.

    Driven against a double that ENFORCES `expected_etag`, because the in-memory
    backend does not: two writers holding the same etag both succeed there (probed
    directly — zero conflicts in 20 rounds), so a race test against it proves
    nothing about the code and fails at random. The double models what a real
    backend promises — compare-and-set under a lock — which is the contract this
    code is written against.
    """
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    members = {f"f{i:03d}.md": f"body {i}".encode() for i in range(120)}
    run_id = coord.enqueue(
        collection_id=cid, zip_data=_archive(members), mode="overwrite", user="u"
    )
    # Seed the join directly rather than via `_split`: that enqueues real jobs, and a
    # consumer left running by an earlier test drains them, finalizes, and these
    # threads then find `run.finished` and return without ever racing.
    coord._patch(run_id, "u", total=3)  # 50 / 50 / 20

    rm = coord._run_rm
    _enforce_etags(monkeypatch, rm)
    barrier = threading.Barrier(3, timeout=20)
    parked = threading.local()
    parked_modify = rm.modify

    def modify_at_barrier(resource_id, data, **kw):
        # Park each thread once on the way INTO the write, so all three hold an etag
        # read before any write landed — the lost-update setup. (Parking on the first
        # READ instead let the CAS's own re-read stagger them, and the test then
        # passed with the CAS deleted, which is no guard at all.)
        if not getattr(parked, "done", False):
            parked.done = True
            barrier.wait()
        return parked_modify(resource_id, data, **kw)

    monkeypatch.setattr(rm, "modify", modify_at_barrier)
    raised: list[BaseException] = []

    def work(index: int) -> None:
        try:
            coord._process(
                ImportPayload(
                    run_id=run_id,
                    kind="process",
                    member_start=index * 50,
                    member_end=index * 50 + 50,
                    batch_index=index,
                ),
                "u",
            )
        except BaseException as exc:  # noqa: BLE001 - reported below
            raised.append(exc)

    try:
        threads = [threading.Thread(target=work, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        for t in threads:
            assert not t.is_alive(), "a batch never finished"

    assert not raised, f"a batch raised instead of recording: {raised!r}"
    run = _run_of(spec, run_id)
    assert len(run.done) + len(run.failed) == run.total, (
        f"join slots lost: done={run.done} failed={run.failed} total={run.total}"
    )
    assert run.written == 120, f"written undercounts: {run.written}"


async def test_finalize_runs_once_even_if_two_finishers_race(monkeypatch):
    """Two batches can both see the last slot close, and a broker can redeliver a
    finalize job. Without a claimed gate each finalizer restores the manifest's cards
    against a snapshot taken before the other wrote — which is exactly the
    duplicate-card defect #701 closed."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({}, cards=[{"keys": ["M4"], "title": "M4", "body": "edge"}]),
        mode="overwrite",
        user="u",
    )
    # Same reason as above: no enqueued jobs, so nothing but these two threads can
    # reach finalize.
    coord._patch(run_id, "u", total=0)
    _enforce_etags(monkeypatch, coord._run_rm)

    barrier = threading.Barrier(2, timeout=20)

    def finalize():
        barrier.wait()
        coord._finalize(run_id, "u")

    threads = [threading.Thread(target=finalize) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert [c.keys for c in _cards(spec, cid)] == [["M4"]], "finalize ran twice"
