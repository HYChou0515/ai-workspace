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
from workspace_app.kb.import_jobs import (
    MAX_ERROR_LINES,
    ImportCoordinator,
    ImportPayload,
    ImportRun,
)
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

    # Park on the way INTO the write, not before `_finalize`: both finishers must
    # still be holding a pre-claim view when they try to claim. Parking earlier lets
    # the first one finish outright, and the second then returns on the cheap
    # `finished` check without ever exercising the gate — green, guarding nothing.
    # Count how many finalizers reach the card restore. Asserting on the resulting
    # CARDS cannot tell the two cases apart: `restore_cards` is idempotent, so a
    # second one that runs AFTER the first merely updates. The duplicate only appears
    # when they overlap — and the gate's whole job is that a second one never runs at
    # all, which is what this counts.
    import workspace_app.kb.import_jobs as import_jobs_module

    restores: list[int] = []
    lock = threading.Lock()
    real_restore = import_jobs_module.restore_cards

    def counted_restore(*a, **kw):
        with lock:
            restores.append(1)
        return real_restore(*a, **kw)

    monkeypatch.setattr(import_jobs_module, "restore_cards", counted_restore)

    # Park between the read and the claim. `_finalize` returns early on an already
    # finished run — a cheap guard against redelivery — so parking at ENTRY lets the
    # first finisher complete and the second bounce off that check, never reaching
    # the gate this test exists to exercise. `_archive_of` sits exactly between the
    # two, so both finishers hold a not-yet-finished view when they try to claim.
    start = threading.Barrier(2, timeout=20)
    real_archive_of = coord._archive_of

    def archive_at_barrier(run):
        data = real_archive_of(run)
        start.wait()
        return data

    monkeypatch.setattr(coord, "_archive_of", archive_at_barrier)

    def finalize():
        coord._finalize(run_id, "u")

    threads = [threading.Thread(target=finalize) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(restores) == 1, f"finalize ran {len(restores)} times"
    assert [c.keys for c in _cards(spec, cid)] == [["M4"]]


async def test_a_redelivered_batch_does_not_double_count():
    """The queue is at-least-once, so the same batch arrives twice. Its slot is
    already closed, so the second delivery must add nothing — otherwise `written`
    drifts above the document count and `done` grows past `total`."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"alpha", "b.md": b"beta"}),
        mode="overwrite",
        user="u",
    )
    coord._patch(run_id, "u", total=1)
    payload = ImportPayload(run_id=run_id, kind="process", member_start=0, member_end=50)

    coord._process(payload, "u")
    coord._process(payload, "u")  # redelivered

    run = _run_of(spec, run_id)
    assert run.done == [0]
    assert run.written == 2, f"a redelivery double-counted: written={run.written}"


async def test_a_batch_redelivered_after_finalize_is_a_no_op():
    """A finished run must not be written into again — the archive is gone, so the
    work cannot be redone, and touching the run would resurrect a closed import."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = coord.enqueue(
        collection_id=cid, zip_data=_archive({"a.md": b"alpha"}), mode="overwrite", user="u"
    )
    await coord.aclose()
    assert _run_of(spec, run_id).finished

    coord._process(ImportPayload(run_id=run_id, kind="process", member_start=0, member_end=50), "u")

    run = _run_of(spec, run_id)
    assert run.finished and run.archive is None


async def test_a_collection_deleted_mid_import_does_not_crash_the_worker():
    """`collection_id` cascades, so deleting the collection takes the run with it.
    Every step then reads nothing and returns — a vanished run is a finished job,
    not an error to retry forever."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = coord.enqueue(
        collection_id=cid, zip_data=_archive({"a.md": b"alpha"}), mode="overwrite", user="u"
    )
    coord._patch(run_id, "u", total=1)
    spec.get_resource_manager(Collection).delete(cid)

    coord._process(ImportPayload(run_id=run_id, kind="process", member_start=0, member_end=50), "u")
    coord._finalize(run_id, "u")
    coord._split(run_id, "u")


async def test_a_member_escaping_the_archive_root_is_dropped():
    """Same fence as the synchronous path: a zip-slip member is not a document."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)

    coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"../escape.md": b"nope", "ok.md": b"fine"}),
        mode="overwrite",
        user="u",
    )
    await coord.aclose()

    assert [d.path for d in _docs(spec, cid)] == ["ok.md"]


async def test_an_archive_with_no_manifest_still_imports_its_documents():
    """A plain zip is a batch folder upload — no settings, no cards, just files."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.md", b"alpha")

    coord.enqueue(collection_id=cid, zip_data=buf.getvalue(), mode="overwrite", user="u")
    await coord.aclose()

    assert [d.path for d in _docs(spec, cid)] == ["a.md"]
    assert _cards(spec, cid) == []


async def test_directory_entries_in_the_archive_are_not_documents():
    """Some zip writers emit explicit directory entries. They are structure, not
    content — counting them would inflate `members` and queue empty writes."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("M4/"), b"")  # a directory entry
        zf.writestr("M4/a.md", b"alpha")

    run_id = coord.enqueue(collection_id=cid, zip_data=buf.getvalue(), mode="overwrite", user="u")
    await coord.aclose()

    assert _run_of(spec, run_id).members == 1
    assert [d.path for d in _docs(spec, cid)] == ["M4/a.md"]


async def test_a_vanished_run_is_a_finished_job_not_an_error():
    """Every step reads the run first. When it is gone — the collection cascaded
    away — the step returns rather than raising: a job that keeps failing is
    redelivered forever over work nobody wants any more."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = coord.enqueue(
        collection_id=cid, zip_data=_archive({"a.md": b"alpha"}), mode="overwrite", user="u"
    )
    spec.get_resource_manager(ImportRun).permanently_delete(run_id)

    coord._split(run_id, "u")
    coord._process(ImportPayload(run_id=run_id, kind="process", member_start=0, member_end=50), "u")
    coord._finalize(run_id, "u")
    coord._patch(run_id, "u", total=9)  # the CAS path, on a row that is not there


async def test_the_second_finalizer_does_no_work():
    """The gate is claimed, so a finalize arriving after the winner returns without
    restoring the cards a second time."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({}, cards=[{"keys": ["M4"], "title": "M4", "body": "edge"}]),
        mode="overwrite",
        user="u",
    )
    coord._patch(run_id, "u", total=0)

    coord._finalize(run_id, "u")
    coord._finalize(run_id, "u")  # redelivered, gate already claimed

    assert [c.keys for c in _cards(spec, cid)] == [["M4"]]


async def test_aclose_on_an_idle_coordinator_returns_at_once():
    """Nothing queued and no consumer started — there is nothing to drain, and
    starting one just to stop it would leak a thread per call."""
    spec = make_spec(default_user="u")
    coord = _coordinator(spec)

    await coord.aclose()

    assert not coord.consuming


async def test_two_members_sharing_a_name_are_both_read():
    """A malformed archive can hold the same name twice. Reading by NAME resolves to
    the last entry, so one member would be written twice and the other never — the
    synchronous path reads entries, and so must this one."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dup.md", b"first")
        zf.writestr("dup.md", b"second")  # same name, second entry

    run_id = coord.enqueue(collection_id=cid, zip_data=buf.getvalue(), mode="overwrite", user="u")
    await coord.aclose()

    run = _run_of(spec, run_id)
    assert run.members == 2  # both entries are members, not one
    assert run.written == 2


async def test_the_failure_list_is_capped_but_the_totals_are_not(monkeypatch):
    """A wholly-failing archive must not put one row's worth of strings per document
    into a row every poll returns. The lines are a sample; `written` vs `members`
    stays the verdict."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)

    def always_fail(**kw):
        raise OSError("disk said no")

    monkeypatch.setattr(coord._ingestor, "store_file", always_fail)
    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({f"f{i:03d}.md": b"x" for i in range(MAX_ERROR_LINES + 20)}),
        mode="overwrite",
        user="u",
    )
    await coord.aclose()

    run = _run_of(spec, run_id)
    assert len(run.errors) == MAX_ERROR_LINES
    assert run.members == MAX_ERROR_LINES + 20
    assert run.written == 0  # the verdict survives the cap


async def test_the_loser_of_a_claim_conflict_restores_nothing(monkeypatch):
    """The OTHER finalize race: the loser's compare-and-swap actually conflicts.

    `test_finalize_runs_once_even_if_two_finishers_race` lets the two finishers
    reach the claim in any order, so the loser usually re-reads AFTER the winner
    wrote and bounces off `current.finished` on its FIRST attempt — a path that
    never sets `claimed` at all. That ordering leaves the reset inside `claim`
    unproven: delete the reset and that test still passes (probed, not assumed).

    This one forces the ordering the reset exists for. Both finishers read, then
    park on the way into the write, so the loser attempts with a stale etag and
    gets a PreconditionFailedError. It retries, re-reads, and sees a finished run —
    and `claimed` must not still carry the True its first attempt set, or the loser
    correctly abandons the write and then restores the cards anyway.
    """
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({}, cards=[{"keys": ["M4"], "title": "M4", "body": "edge"}]),
        mode="overwrite",
        user="u",
    )
    coord._patch(run_id, "u", total=0)
    _enforce_etags(monkeypatch, coord._run_rm)

    import workspace_app.kb.import_jobs as import_jobs_module

    restores: list[int] = []
    count_lock = threading.Lock()
    real_restore = import_jobs_module.restore_cards

    def counted_restore(*a, **kw):
        with count_lock:
            restores.append(1)
        return real_restore(*a, **kw)

    monkeypatch.setattr(import_jobs_module, "restore_cards", counted_restore)

    # Park on the way INTO the first write only. Both finishers have then completed
    # their CAS read holding the same etag, which is what makes the second write
    # CONFLICT rather than simply observe an already-finished run.
    barrier = threading.Barrier(2, timeout=20)
    parked = threading.local()
    guarded_modify = coord._run_rm.modify

    def modify_once_at_barrier(resource_id, data, **kw):
        if not getattr(parked, "done", False):
            parked.done = True
            barrier.wait()
        return guarded_modify(resource_id, data, **kw)

    monkeypatch.setattr(coord._run_rm, "modify", modify_once_at_barrier)

    threads = [threading.Thread(target=lambda: coord._finalize(run_id, "u")) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a finalizer never finished"

    assert len(restores) == 1, f"the losing finalizer still restored: {len(restores)} runs"
    assert [c.keys for c in _cards(spec, cid)] == [["M4"]]
