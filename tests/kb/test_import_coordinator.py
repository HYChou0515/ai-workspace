"""#715: importing a large archive must not hold the HTTP request open.

The synchronous path writes every document before it answers, which is right for
restoring a backup you exported yourself — a person clicks it and waits a few
seconds. It is wrong for a machine pushing a prepared knowledge base: a 207 MB
archive spent ten minutes in it and came back 504. These cover the asynchronous
contract that replaces it for that use: stage the archive, return a run id, do
the writing on a worker.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
import zipfile

import msgspec
import pytest
from specstar import QB, SpecStar
from specstar.types import Binary, BlobStreamInfo, PreconditionFailedError, RevisionStatus

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.collection_export import MANIFEST_DIR
from workspace_app.kb.collection_import import import_collection
from workspace_app.kb.doc_id import canonical_path
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.import_jobs import (
    MAX_ERROR_LINES,
    ImportCoordinator,
    ImportJob,
    ImportPayload,
    ImportRun,
    _members_of,
)
from workspace_app.kb.index_coordinator import IndexCoordinator
from workspace_app.kb.index_jobs import IndexJob
from workspace_app.kb.ingest import Ingestor
from workspace_app.perm.model import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.groups import Group
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


def _coordinator(spec: SpecStar, *, superusers: frozenset[str] = frozenset()) -> ImportCoordinator:
    """The real IndexCoordinator, never consumed.

    A hand-written stub here would only ever prove "we called enqueue", which
    survives any change to what enqueueing actually requires. The real one leaves
    IndexJob rows behind, so the assertion is about the queue that exists rather
    than about our own call."""
    ing = Ingestor(
        spec, chunker=FixedTokenChunker(max_tokens=64), embedder=HashEmbedder(dim=EMBED_DIM)
    )
    return ImportCoordinator(
        spec,
        ingestor=ing,
        index_coordinator=IndexCoordinator(spec, ing),
        superusers=superusers,
    )


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

    # Park between materialising the archive and the claim. `_finalize` returns
    # early on an already finished run — a cheap guard against redelivery — so
    # parking at ENTRY lets the first finisher complete and the second bounce off
    # that check, never reaching the gate this test exists to exercise.
    # `_archive_file` sits exactly between the two, so both finishers hold a
    # not-yet-finished view when they try to claim.
    start = threading.Barrier(2, timeout=20)
    real_archive_file = coord._archive_file

    @contextlib.contextmanager
    def archive_at_barrier(run):
        with real_archive_file(run) as fh:
            start.wait()
            yield fh

    monkeypatch.setattr(coord, "_archive_file", archive_at_barrier)

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


async def test_the_archive_is_streamed_to_disk_when_the_blob_store_can(monkeypatch):
    """#715 asked for the archive to be spilled to a file, not held in memory.

    A double, because the contract is the other side's: `MemoryBlobStore` has no
    `get_stream`, so under a bare test spec the streaming branch never runs — while
    in production (disk and S3 stores both implement it) it is the ONLY branch. A
    test that exercised just the fallback would leave the deployed path unproven.

    Asserting the documents landed is not enough on its own: the fallback lands
    them too. So this also asserts the stream was consumed AND that nothing
    restored the whole blob — otherwise a silent regression to loading the archive
    into memory would keep this test green.
    """
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    zip_data = _archive({"a.md": b"alpha", "b.md": b"beta"})
    coord.enqueue(collection_id=cid, zip_data=zip_data, mode="overwrite", user="u")

    served: list[int] = []
    restored: list[int] = []
    real_restore_binary = coord._run_rm.restore_binary

    def chunks():
        for i in range(0, len(zip_data), 64):
            served.append(1)
            yield zip_data[i : i + 64]

    # A NEW generator per call: split, process and finalize each materialise the
    # archive, and a single shared iterator would be exhausted after the first.
    monkeypatch.setattr(
        coord._run_rm,
        "get_blob_stream",
        lambda file_id: BlobStreamInfo(chunks(), size=len(zip_data)),
    )

    def counted_restore_binary(run):
        restored.append(1)
        return real_restore_binary(run)

    monkeypatch.setattr(coord._run_rm, "restore_binary", counted_restore_binary)

    await coord.aclose()

    assert served, "the blob store offered a stream and nothing consumed it"
    assert not restored, "loaded the whole blob despite a store that can stream"
    assert sorted(d.path for d in _docs(spec, cid)) == ["a.md", "b.md"]


async def test_the_materialised_archive_is_handed_over_rewound():
    """`_archive_file` yields a handle, and a handle has a position.

    Its three callers all pass it to `zipfile`, which seeks absolutely to find the
    central directory and so cannot tell a rewound handle from one left at EOF —
    delete the `seek(0)` and every other test still passes (probed). That makes the
    rewind an interface contract rather than a guard, and this is the test that
    holds the contract for the next caller, who may simply read it.
    """
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    zip_data = _archive({"a.md": b"alpha"})
    run_id = coord.enqueue(collection_id=cid, zip_data=zip_data, mode="overwrite", user="u")
    run = _run_of(spec, run_id)

    with coord._archive_file(run) as fh:
        assert fh.tell() == 0, "the handle was not rewound before being handed over"
        assert fh.read() == zip_data


def _worker_spec() -> SpecStar:
    """A spec whose AMBIENT user is the worker pod — NOT the uploader.

    Every other spec in this file is `make_spec(default_user="u")` driven with
    `user="u"`, so requester, ambient user and route caller are one string. That
    makes every rule phrased around IDENTITY untested by construction, and it is
    not what the deployment this feature exists for looks like: `python -m
    workspace_app.worker` sets the ambient user to `server.default_user`, while
    the requester is whoever uploaded. The gap hid two defects behind a green
    suite — see the two tests below.
    """
    return make_spec(default_user="worker-pod")


async def test_a_worker_running_as_itself_still_completes_an_upload_by_someone_else():
    """The run's own permission fence must not lock out the worker that drains it.

    `ImportRun` is owner-only for writes (#715 round 1). A worker pod writes under
    the AMBIENT identity unless told otherwise, and that identity is not the
    uploader — so every `_cas` and every `_enqueue_step` would be refused, and the
    import would stall after a 202 with nothing on the run to say why. That is the
    exact failure this feature exists to remove, so it gets a test rather than a
    comment.
    """
    spec = _worker_spec()
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"alpha"}, cards=[{"keys": ["M4"], "title": "M4", "body": "e"}]),
        mode="overwrite",
        user="alice",
    )
    await coord.aclose()

    run = _run_of(spec, run_id)
    assert run.finished, "the run never finalized under a worker-pod ambient identity"
    assert run.written == run.members == 1
    assert [d.path for d in _docs(spec, cid)] == ["a.md"]
    assert [c.keys for c in _cards(spec, cid)] == [["M4"]]


async def test_the_worker_refuses_to_write_where_the_requester_may_not():
    """The async path must re-check the collection, because the route no longer can.

    The synchronous import authorises `add_content` in the request. The async one
    moves the write to a worker whose only authority is `ImportRun.collection_id`
    — a field the run's own owner may PATCH. Without a check at the write itself,
    anyone can stage an import and re-point it at a collection they cannot even
    read, and the worker performs it: documents land, and `restore_cards` OVERWRITES
    that collection's context cards by key. Context cards are injected into agent
    prompts, so this is persistent prompt injection against another tenant, not
    just an unwanted file.
    """
    spec = _worker_spec()
    crm = spec.get_resource_manager(Collection)
    with crm.using(user="alice"):
        victim = crm.create(
            Collection(name="alice-private", permission=Permission(visibility="private"))
        ).resource_id

    coord = _coordinator(spec)
    coord.enqueue(
        collection_id=victim,
        zip_data=_archive(
            {"PWNED.md": b"x"}, cards=[{"keys": ["POLICY"], "title": "p", "body": "b"}]
        ),
        mode="overwrite",
        user="mallory",
    )
    await coord.aclose()

    assert [d.path for d in _docs(spec, victim)] == [], "mallory wrote into a private collection"
    assert _cards(spec, victim) == [], "mallory overwrote another tenant's context cards"


async def _import_into(spec: SpecStar, cid: str, *, user: str, superusers=frozenset()) -> str:
    """Drive one single-document import to completion and return its run id."""
    coord = _coordinator(spec, superusers=superusers)
    run_id = coord.enqueue(
        collection_id=cid,
        zip_data=_archive({"a.md": b"alpha"}, cards=[{"keys": ["K"], "title": "K", "body": "b"}]),
        mode="overwrite",
        user=user,
    )
    await coord.aclose()
    return run_id


def _private_collection(spec: SpecStar, owner: str) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using(user=owner):
        return crm.create(
            Collection(name="private", permission=Permission(visibility="private"))
        ).resource_id


async def test_a_superuser_may_import_into_a_collection_they_do_not_own():
    """The refusal must not be stricter than the route it stands in for.

    `create_app` gates every collection route on `settings.server.superusers`; a
    worker that decided `add_content` without that set would refuse imports the
    HTTP path allows, and the answer would depend on which pod ran the job. This
    is the positive half of the same gate `test_the_worker_refuses_to_write...`
    checks — a knob that only ever denies is indistinguishable from a hardcoded no.
    """
    spec = _worker_spec()
    victim = _private_collection(spec, "alice")

    await _import_into(spec, victim, user="root", superusers=frozenset({"root"}))

    assert [d.path for d in _docs(spec, victim)] == ["a.md"]


async def test_a_group_grant_on_the_collection_is_honoured_by_the_worker():
    """`group:<id>` has to keep working, or the fix quietly narrows who can import.

    The route resolves the caller's groups (`groups_of`) before authorizing, so a
    collection shared with a TEAM is reachable. Re-deriving the verdict worker-side
    from the requester's id ALONE would look correct in every test where the
    requester is also the owner, and would break exactly the collections that are
    shared — the ones an archive import is most likely aimed at.
    """
    spec = _worker_spec()
    grm = spec.get_resource_manager(Group)
    with grm.using(user="alice"):
        gid = grm.create(Group(name="team", members=["bob"])).resource_id
    crm = spec.get_resource_manager(Collection)
    with crm.using(user="alice"):
        cid = crm.create(
            Collection(
                name="shared",
                # `restricted`, not `private`: a grant list only bites at that tier
                # (`private` denies everyone but the owner, by design). The subject
                # is `group:` + the group's own id, matching `tests/api/test_groups`
                # and what `Actor.subjects` builds via `group_subject`.
                permission=Permission(visibility="restricted", add_content=[f"group:{gid}"]),
            )
        ).resource_id

    await _import_into(spec, cid, user="bob")

    assert [d.path for d in _docs(spec, cid)] == ["a.md"], "a group grant did not reach the worker"


async def test_a_refused_run_stops_and_says_so_instead_of_polling_forever():
    """A refusal the caller cannot see is the silence #715 exists to remove.

    The documented verdict is `written` vs `members` with `errors` for the detail,
    so a refusal has to land there — and the run has to FINISH, or the poller waits
    on a job that will be refused identically on every retry while 200 MB stays
    staged.
    """
    spec = _worker_spec()
    victim = _private_collection(spec, "alice")

    run_id = await _import_into(spec, victim, user="mallory")

    run = _run_of(spec, run_id)
    assert run.finished, "a refused run must not leave the caller polling"
    assert run.written == 0
    assert run.archive is None, "the staged archive outlives a refusal it cannot survive"
    assert run.errors and "may not add content" in run.errors[0]
    # Refused at SPLIT, before the fan-out: a check left to the write steps alone
    # would still be safe, but it would seed `total` and put one job per batch on
    # the broker for every one of them to refuse — a rejected 200 MB import
    # costing 60 deliveries instead of one.
    assert run.total == 0, "a refused run fanned its batches out anyway"
    jrm = spec.get_resource_manager(ImportJob)
    kinds = []
    for r in jrm.list_resources(QB.all().build()):
        assert isinstance(r.data, ImportJob)
        kinds.append(r.data.payload.kind)
    assert "process" not in kinds, f"refused at split, yet process jobs were queued: {kinds}"


async def test_a_duplicate_name_writes_the_ENTRY_it_read_not_the_last_one():
    """The counting half of `zf.read(info)` has a test; the READING half did not.

    `test_two_members_sharing_a_name_are_both_read` pins that both entries are
    MEMBERS — and it passes just as happily if the bytes written come from
    `zf.read(info.filename)`, which resolves a duplicate name to the LAST entry.
    Under `overwrite` that difference is invisible (the last write wins either
    way), which is why it stayed uncovered.

    `skip` makes it observable: the first entry is written because the document
    does not exist yet, and the second is skipped because now it does. So the
    stored bytes must be the FIRST entry's. Reading by name would store the
    second entry's bytes under the first entry's decision — silently serving
    content the archive never placed at that path.
    """
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dup.md", b"first")
        zf.writestr("dup.md", b"second")

    run_id = coord.enqueue(collection_id=cid, zip_data=buf.getvalue(), mode="skip", user="u")
    await coord.aclose()

    run = _run_of(spec, run_id)
    assert run.members == 2
    docs = _docs(spec, cid)
    assert len(docs) == 1, "skip must leave one document, not two"
    rm = spec.get_resource_manager(SourceDoc)
    stored = rm.restore_binary(docs[0]).content
    assert stored is not None and stored.data == b"first", (
        "the written bytes came from the LAST entry sharing the name, not the one read"
    )


def _staged_run(spec: SpecStar, cid: str, owner: str) -> str:
    """A run in the state `split` leaves behind: archive staged, `total` seeded,
    nothing written — and pointing at `cid`, which its owner may have re-pointed
    it to a moment ago."""
    rrm = spec.get_resource_manager(ImportRun)
    with rrm.using(user=owner):
        return rrm.create(
            ImportRun(
                collection_id=cid,
                mode="overwrite",
                members=1,
                total=1,
                archive=Binary(
                    data=_archive(
                        {"a.md": b"x"}, cards=[{"keys": ["K"], "title": "K", "body": "b"}]
                    ),
                    content_type="application/zip",
                ),
            )
        ).resource_id


async def test_a_repointed_run_is_refused_at_the_write_step_not_only_at_split():
    """The gate at `split` guards a value that can still change under it.

    `collection_id` stays PATCHable for the run's whole life, and the steps are
    separate broker deliveries — so the owner can stage an import at a collection
    they may write, let split pass, then re-point the run before a document lands.
    `_process` and `_finalize` therefore have to re-derive the verdict rather than
    inherit split's, and this test enters at exactly the state that produces: a
    run already fanned out, now pointing somewhere the requester cannot write, and
    its jobs arriving at `_handle` — the method specstar itself calls.
    """
    spec = _worker_spec()
    victim = _private_collection(spec, "alice")
    coord = _coordinator(spec)
    jrm = spec.get_resource_manager(ImportJob)
    run_id = _staged_run(spec, victim, "mallory")

    steps = (
        ("process", {"member_start": 0, "member_end": 50, "batch_index": 0}),
        ("finalize", {}),
    )
    for kind, extra in steps:
        # A FRESH run per step. Delivering both to one run proves only the first:
        # `_process`'s refusal finishes the run, and `_finalize` then returns at its
        # `run.finished` check without ever reaching its own gate.
        rid = run_id if kind == "process" else _staged_run(spec, victim, "mallory")
        with jrm.using(user="mallory"):
            job = jrm.create(ImportJob(payload=ImportPayload(run_id=rid, kind=kind, **extra)))
        coord._handle(jrm.get(job.resource_id))

    assert [d.path for d in _docs(spec, victim)] == [], "a re-pointed run wrote anyway"
    assert _cards(spec, victim) == [], "a re-pointed run overwrote another tenant's cards"


def _corrupt_the_staged_archive(spec: SpecStar, run_id: str, owner: str) -> None:
    """Replace the staged blob with bytes that are not a zip.

    `enqueue` opens the archive to count members, so a corrupt archive cannot be
    submitted — it can only appear afterwards, which is exactly the case the steps
    have to survive: a blob store that returns something else, a truncated write, a
    restore from a half-written backup."""
    rrm = spec.get_resource_manager(ImportRun)
    cur = rrm.get(run_id)
    assert isinstance(cur.data, ImportRun)
    with rrm.using(user=owner):
        rrm.modify(
            run_id,
            msgspec.structs.replace(
                cur.data, archive=Binary(data=b"not a zip at all", content_type="application/zip")
            ),
            status=RevisionStatus.draft,
        )


async def test_an_unreadable_archive_ends_the_run_instead_of_wedging_it_at_split():
    """`split` had no failure channel at all — only `process` did.

    An archive that cannot be opened made the step raise, which the queue retries
    until the job gives up. The run then sits at `finished=false`, `errors=[]` for
    ever: a caller polling a 202 that never moves and never says why, which is the
    precise failure this feature exists to remove.
    """
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = coord.enqueue(
        collection_id=cid, zip_data=_archive({"a.md": b"x"}), mode="overwrite", user="u"
    )
    _corrupt_the_staged_archive(spec, run_id, "u")

    await coord.aclose()

    run = _run_of(spec, run_id)
    assert run.finished, "an unreadable archive left the run polling for ever"
    assert run.written == 0
    assert run.errors and "archive could not be read" in run.errors[0], run.errors


async def test_an_unreadable_archive_closes_the_batch_slot_instead_of_wedging_finalize():
    """`process`'s containment is per MEMBER, so it never covered the archive itself.

    A batch that raises before its loop closes no slot, and the finalize gate counts
    slots — so cards are never restored, the staged archive is never released, and
    the run reports `finished: false` with nothing in `errors` for ever, even though
    every other batch is done.
    """
    spec = _worker_spec()
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = _staged_run(spec, cid, "alice")
    _corrupt_the_staged_archive(spec, run_id, "alice")
    jrm = spec.get_resource_manager(ImportJob)
    with jrm.using(user="alice"):
        job = jrm.create(
            ImportJob(
                payload=ImportPayload(
                    run_id=run_id, kind="process", member_start=0, member_end=50, batch_index=0
                )
            )
        )

    coord._handle(jrm.get(job.resource_id))
    await coord.aclose()  # the finalize this batch's closed slot should have queued

    run = _run_of(spec, run_id)
    assert run.errors and "archive could not be read" in run.errors[0], run.errors
    assert run.finished, "the batch closed no slot, so finalize never fired"


async def test_cards_that_fail_to_restore_are_not_reported_as_a_clean_import():
    """The worst shape a failure can take: byte-identical to success.

    `finalize` claims the run — setting `finished` and releasing the archive —
    BEFORE restoring the cards, because two finalizers must not both restore
    (#701's duplicate cards). So a `restore_cards` that raises cannot be retried,
    and with no channel of its own it produced `written == members`, `errors: []`,
    `finished: true` and not one card. The documents did land, so this is not a
    refusal: it is one more line in the channel the per-document failures use.
    """
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.md", b"alpha")
        zf.writestr(
            ".kb-collection/manifest.json",
            json.dumps({"version": 1, "context_cards": ["this is not a card object"]}),
        )

    run_id = coord.enqueue(collection_id=cid, zip_data=buf.getvalue(), mode="overwrite", user="u")
    await coord.aclose()

    run = _run_of(spec, run_id)
    assert [d.path for d in _docs(spec, cid)] == ["a.md"], "the documents should still land"
    assert _cards(spec, cid) == [], "the malformed card should not have been created"
    assert run.errors and "context cards were not restored" in run.errors[0], (
        f"a failed card restore reported a clean import: written={run.written} "
        f"members={run.members} errors={run.errors} finished={run.finished}"
    )


async def test_one_unreadable_batch_does_not_finalize_a_run_that_has_batches_left():
    """Closing a slot is not the same as closing the LAST slot.

    The containment above enqueues `finalize` when the failed batch turns out to be
    the last one outstanding. It must ask — an unconditional enqueue would restore
    the cards and release the archive while other batches are still writing, which
    is the duplicate-card gate (#701) reopened from the failure path.
    """
    spec = _worker_spec()
    cid = _collection(spec)
    coord = _coordinator(spec)
    run_id = _staged_run(spec, cid, "alice")
    rrm = spec.get_resource_manager(ImportRun)
    cur = rrm.get(run_id)
    assert isinstance(cur.data, ImportRun)
    with rrm.using(user="alice"):
        rrm.modify(  # two batches, so closing one leaves one outstanding
            run_id, msgspec.structs.replace(cur.data, total=2), status=RevisionStatus.draft
        )
    _corrupt_the_staged_archive(spec, run_id, "alice")
    jrm = spec.get_resource_manager(ImportJob)
    with jrm.using(user="alice"):
        job = jrm.create(
            ImportJob(
                payload=ImportPayload(
                    run_id=run_id, kind="process", member_start=0, member_end=50, batch_index=0
                )
            )
        )

    coord._handle(jrm.get(job.resource_id))

    run = _run_of(spec, run_id)
    assert run.errors and "archive could not be read" in run.errors[0], run.errors
    assert not run.finished, "finalize fired with a batch still outstanding"
    assert run.archive is not None, "the archive was released while a batch was still to run"


async def test_a_redelivered_refusal_does_not_append_a_second_reason():
    """`_refuse` is claimed to be idempotent — `_finalize` leans on it by name.

    A refused step can be redelivered (the broker retries, and two finalizers can
    reach the unopenable archive at once). Without the `finished` abort, each
    delivery would add another identical line to `errors`, so a caller reading the
    reason would see the same sentence N times and infer N distinct failures.
    """
    spec = _worker_spec()
    victim = _private_collection(spec, "alice")
    run_id = _staged_run(spec, victim, "mallory")
    coord = _coordinator(spec)

    coord._refuse(run_id, "mallory", "first")
    coord._refuse(run_id, "mallory", "second")

    run = _run_of(spec, run_id)
    assert run.errors == ["first"], f"a redelivery appended again: {run.errors}"
    assert run.finished


def _hostile_archive() -> bytes:
    """One archive holding every member class the predicate has to judge."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("good.md", b"keep me")
        zf.writestr("nested/also-good.md", b"keep me too")
        zf.writestr("dup.md", b"first")
        zf.writestr("dup.md", b"second")  # same name, two entries
        zf.writestr("../escaped.md", b"zip-slip")  # must never become a document
        zf.writestr("adir/", b"")  # a directory entry, not a file
        zf.writestr(MANIFEST_PATH, json.dumps({"version": 1, "context_cards": []}))
        zf.writestr(f"{MANIFEST_DIR}other.bin", b"reserved dir, still metadata")  # DIR ends in /
    return buf.getvalue()


async def test_both_importers_select_the_same_members_from_a_hostile_archive():
    """#715's locked decision 5 — the two paths share the restore rules.

    The member predicate is security-relevant (it is what drops zip-slip) and it
    was written out twice, once per importer. Two copies of a rule is one rule
    that will be wrong: a hardening applied to the path someone happens to be
    reading leaves the other exactly as it was. This asserts BOTH halves — the
    concrete verdict, so a wrong predicate fails, and that the two paths reach it
    identically, so a re-inlined copy fails too.
    """
    data = _hostile_archive()
    expected = ["dup.md", "dup.md", "good.md", "nested/also-good.md"]

    # asynchronous: what `split` would fan out
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        chosen = sorted(canonical_path(i.filename) for i in _members_of(zf))
    assert chosen == expected, chosen

    # synchronous: what `import_collection` actually writes
    spec = make_spec(default_user="u")
    ing = Ingestor(
        spec, chunker=FixedTokenChunker(max_tokens=64), embedder=HashEmbedder(dim=EMBED_DIM)
    )
    result = import_collection(
        spec=spec,
        ingestor=ing,
        index_coordinator=IndexCoordinator(spec, ing),
        zip_data=data,
        user="u",
        fallback_name="hostile",
    )
    cid = result.collection_id
    written = sorted(d.path for d in _docs(spec, cid))
    # `dup.md` collapses to ONE document — two entries, one path, last write wins.
    assert written == sorted(set(expected)), written
    assert set(chosen) == set(written), (
        f"the two importers disagree about what is a document: {set(chosen) ^ set(written)}"
    )


def test_batching_is_decided_by_the_content_not_by_the_zip_write_order():
    """`split` hands out batches as index RANGES into `_members_of`.

    So what "members 50-99" denotes has to follow from WHAT is in the archive,
    not from the order a particular zip writer happened to emit. Two archives
    holding the same documents in different write order must split identically,
    or the same content produces different batches and a `member_start` in an
    error line means nothing outside the one run that produced it.
    """
    names = ["b.md", "a/deep.md", "z.md", "a/shallow.md", "c.md"]
    orders = (names, list(reversed(names)))
    layouts = []
    for order in orders:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name in order:
                zf.writestr(name, b"x")
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
            layouts.append([i.filename for i in _members_of(zf)])

    assert layouts[0] == layouts[1], (
        f"the same documents split differently depending on zip write order: {layouts}"
    )
    assert layouts[0] == sorted(names), layouts[0]
