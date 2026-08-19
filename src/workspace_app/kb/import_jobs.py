"""Issue #715: importing a collection archive without holding the request open.

The synchronous importer (``collection_import.import_collection``) writes every
document before it answers. Its own module docstring states why that is right for
what it was built for — *reconstruct a collection from an exported zip*: a person
clicks restore, waits a few seconds, and the size is bounded by the collection
they exported.

A machine pushing a prepared knowledge base is a different contract. Nobody is
watching, the size is decided by the sender, and a 207 MB archive spent ten
minutes in that endpoint before a gateway cut it. Raising the timeout would not
fix it: a dropped connection loses the whole operation with no resume, the caller
sees no progress, and a retry repeats everything.

So this is the asynchronous half, following the shape the codebase already uses
for background fan-out (``CardGenRun``/``CardGenJob``/``CardGenCoordinator``, and
``IndexJob(kind="collection")``, whose docstring gives the same reason: leave one
row behind and return instead of walking N documents inside the HTTP request):

- the ROUTE creates the collection (one row, cheap) and stages the archive on an
  :class:`ImportRun`, then enqueues one ``split`` job and returns. The caller gets
  the collection id immediately, so the collection appears — empty and filling —
  rather than existing only once the work is done;
- ``split`` reads the archive's member list, seeds ``total``, and fans out
  ``process`` jobs over BATCHES of members (not one job per member: an archive can
  hold thousands, and a job per file would flood the broker);
- each ``process`` job writes its batch and records the batch's members in
  ``done``/``failed`` by CAS against the run;
- ``finalize`` runs once, restores the manifest's context cards, and releases the
  staged archive.

**Known limitation.** specstar's ``Binary`` takes bytes, so staging still costs one
in-memory copy of the archive in the API pod while the blob is written. The
synchronous path had the same peak (``await file.read()``), so this is not a
regression — but #715's goal of keeping the archive out of memory entirely is NOT
met by this, and pretending otherwise would leave the next person to discover it
under load.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

import msgspec
from specstar import QB, Schema
from specstar.types import (
    Binary,
    Job,
    OnDelete,
    PreconditionFailedError,
    Ref,
    ResourceNotFoundError,
    RevisionStatus,
    TaskStatus,
)

from .collection_export import MANIFEST_DIR, MANIFEST_PATH
from .collection_import import doc_exists, restore_cards
from .doc_id import canonical_path

if TYPE_CHECKING:  # pragma: no cover - typing only
    from specstar import SpecStar

    from .index_coordinator import IndexCoordinator
    from .ingest import Ingestor

# How many archive members one `process` job writes. Sized so a batch stays well
# inside a broker's consumer-ack timeout on a slow backend, while keeping the job
# count sane for an archive holding thousands of files — the same trade the index
# fan-out makes with its unit batches.
MEMBERS_PER_BATCH = 50

# How many failure lines the run keeps. One line per failed document is what makes a
# partial import actionable, but a wholly-failing 3000-member archive would put 3000
# strings in one row that every poll then returns. Past this the count still tells
# the caller how many failed (`members - written`); the lines are a sample.
MAX_ERROR_LINES = 100

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02  # aclose() poll cadence while the queue drains
_MAX_CAS_RETRIES = 20  # same ceiling as the card-gen run store


class ImportPayload(msgspec.Struct):
    """One step of restoring an archive into ``run_id``'s collection.

    - ``split`` (default): read the member list, seed the run's ``total``, and fan
      out one ``process`` job per batch.
    - ``process``: write the half-open member range ``[member_start, member_end)``
      and record ``batch_index`` on the run.
    - ``finalize``: restore the manifest's context cards and release the archive.
      Runs exactly once, gated by a CAS-claimed flag on the run.
    """

    run_id: str
    kind: str = "split"  # split | process | finalize
    member_start: int = 0
    member_end: int = 0
    batch_index: int = 0


class ImportJob(Job[ImportPayload]):
    """A queued step of an archive import. The ``split`` and ``finalize`` jobs carry
    ``partition_key = run_id`` so a run's head and tail serialise; the fanned-out
    ``process`` jobs carry ``None`` so they parallelise across worker pods — the CAS
    join on :class:`ImportRun`, not the queue, is what guards correctness (the
    RabbitMQ backend ignores ``partition_key``)."""


class ImportRun(msgspec.Struct):  # → resource "import-run"
    """The durable state of one archive import: the staged archive, the fan-out
    join state, and the per-batch outcome the caller polls.

    Mirrors :class:`~workspace_app.kb.card_gen.CardGenRun`. ``done``/``failed`` hold
    BATCH indices (a batch is ``MEMBERS_PER_BATCH`` members), and ``errors`` carries
    up to ``MAX_ERROR_LINES`` ``path: reason`` lines — a caller who could not watch
    the request needs to see a half-applied import as one, not just "finished".
    Compare ``written`` with ``members`` for the verdict; ``errors`` is the detail.

    ``archive`` is cleared at finalize: a 200 MB blob per import is not something to
    keep once it is unpacked. It is held while the run could still be retried.
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    mode: str = "overwrite"
    archive: Binary | None = None
    # The JOIN state counts batches — the finalize gate only needs every slot closed.
    total: int = 0  # number of BATCHES, seeded by split
    done: list[int] = msgspec.field(default_factory=list)
    failed: list[int] = msgspec.field(default_factory=list)
    # The CALLER-facing outcome counts documents: how many the archive held, how many
    # landed, and one "path: reason" line per document that did not. A batch index is
    # not something anyone can act on.
    members: int = 0
    written: int = 0
    errors: list[str] = msgspec.field(default_factory=list)
    finished: bool = False


def _members_of(zip_data: bytes) -> list[zipfile.ZipInfo]:
    """The archive's document members, in a stable order.

    The manifest is metadata, never a document; a member that escapes its root is
    dropped exactly as the synchronous path drops it. Sorted so the batches a
    ``split`` hands out are reproducible — a retry must address the same members.
    Entries rather than names, so a duplicated name still yields two members.
    """
    out: list[zipfile.ZipInfo] = []
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name == MANIFEST_PATH or name.startswith(MANIFEST_DIR):
                continue
            try:
                path = canonical_path(name)
            except ValueError:
                continue  # zip-slip
            if path:
                out.append(info)
    # By entry, never by name: a malformed archive can hold the same name twice, and
    # `read(name)` resolves to the last of them — so one member would be written
    # twice and the other never. The synchronous path reads entries; so does this.
    return sorted(out, key=lambda i: (i.filename, i.header_offset))


def _manifest_of(zip_data: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        try:
            raw = zf.read(MANIFEST_PATH)
        except KeyError:
            return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


class ImportCoordinator:
    """Producer + consumer for archive imports (#715).

    Registers :class:`ImportJob` with its handler, so the same object both enqueues
    a run and drains it — the shape every other coordinator here uses.
    """

    def __init__(
        self,
        spec: SpecStar,
        *,
        ingestor: Ingestor,
        index_coordinator: IndexCoordinator,
        message_queue_factory: object | None = None,
    ) -> None:
        self._spec = spec
        self._ingestor = ingestor
        self._index = index_coordinator
        if message_queue_factory is None:
            from specstar.message_queue import SimpleMessageQueueFactory

            message_queue_factory = SimpleMessageQueueFactory()
        spec.add_model(
            Schema(ImportJob, "v1"),
            job_handler=self._handle,
            indexed_fields=["status", "partition_key"],
            message_queue_factory=message_queue_factory,  # ty: ignore[invalid-argument-type]
        )
        self._job_rm = spec.get_resource_manager(ImportJob)
        self._run_rm = spec.get_resource_manager(ImportRun)
        self._consuming = False

    # ── producer ─────────────────────────────────────────────────────
    def enqueue(self, *, collection_id: str, zip_data: bytes, mode: str, user: str) -> str:
        """Stage ``zip_data`` and queue its restore. Returns the RUN id.

        Returns before a single document is written — that is the whole point. The
        collection must already exist (the route creates it, so the caller has its
        id straight away)."""
        # Count the members HERE, not in `split`. Reading a zip's central directory
        # is milliseconds even for a large archive, and the caller should be able to
        # see "1000 documents queued" the moment it is accepted rather than zero
        # until a worker picks the run up — a progress number that starts as a lie
        # is worse than no number.
        with self._run_rm.using(user=user):
            run_id = self._run_rm.create(
                ImportRun(
                    collection_id=collection_id,
                    mode=mode,
                    members=len(_members_of(zip_data)),
                    archive=Binary(data=zip_data, content_type="application/zip"),
                )
            ).resource_id
        with self._job_rm.using(user=user):
            self._job_rm.create(
                ImportJob(
                    payload=ImportPayload(run_id=run_id, kind="split"),
                    partition_key=run_id,
                )
            )
        return run_id

    # ── consumer ─────────────────────────────────────────────────────
    def _handle(self, job) -> None:  # job: Resource[ImportJob]
        """Dispatch one import step by ``kind``, OFF the request. specstar calls this
        with the job RESOURCE positionally, so the payload is one level in and the
        requester rides on the resource meta — which is who the restored documents
        must be credited to, not whichever user the worker happens to run as."""
        payload = job.data.payload
        assert isinstance(payload, ImportPayload)
        requester = job.info.created_by
        if payload.kind == "process":
            self._process(payload, requester)
        elif payload.kind == "finalize":
            self._finalize(payload.run_id, requester)
        else:
            self._split(payload.run_id, requester)

    def _read(self, run_id: str) -> ImportRun | None:
        from specstar.types import ResourceNotFoundError

        try:
            data = self._run_rm.get(run_id).data
        except ResourceNotFoundError:
            return None
        return data if isinstance(data, ImportRun) else None

    def _archive_of(self, run: ImportRun) -> bytes:
        restored = self._run_rm.restore_binary(run).archive
        assert restored is not None and isinstance(restored.data, bytes)
        return restored.data

    def _split(self, run_id: str, requester: str) -> None:
        """Seed the join state and fan the members out in batches."""
        run = self._read(run_id)
        if run is None or run.finished:
            return
        members = _members_of(self._archive_of(run))
        batches = [
            (i, members[s : s + MEMBERS_PER_BATCH])
            for i, s in enumerate(range(0, len(members), MEMBERS_PER_BATCH))
        ]
        self._patch(run_id, requester, total=len(batches))
        if not batches:
            # Nothing to write — cards still have to be restored, so go straight
            # to finalize rather than leaving the run pending forever.
            self._enqueue_step(run_id, "finalize", requester)
            return
        for index, _ in batches:
            start = index * MEMBERS_PER_BATCH
            self._enqueue_step(
                run_id,
                "process",
                requester,
                member_start=start,
                member_end=start + MEMBERS_PER_BATCH,
                batch_index=index,
                partition_key=None,
            )

    def _process(self, payload: ImportPayload, requester: str) -> None:
        """Write one batch of members, then record it on the run."""
        run = self._read(payload.run_id)
        if run is None or run.finished:
            return
        zip_data = self._archive_of(run)
        members = _members_of(zip_data)[payload.member_start : payload.member_end]
        written = 0
        skipped = 0
        failures: list[str] = []
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for info in members:
                # Per MEMBER, not per batch: a batch is a scheduling unit, and one
                # unreadable file must not take its 49 neighbours down with it. The
                # caller needs to know WHICH document did not land — "batch 3 failed"
                # is not something anyone can act on.
                name = info.filename
                path = canonical_path(name)
                if run.mode == "skip" and doc_exists(self._spec, run.collection_id, path):
                    # Same judgement as the synchronous path, from the same function:
                    # `mode` decides a DOCUMENT collision as well as a card one, and
                    # writing anyway would make `skip` mean its opposite for files.
                    skipped += 1
                    continue
                try:
                    doc_id = self._ingestor.store_file(
                        collection_id=run.collection_id,
                        user=requester,
                        path=path,
                        data=zf.read(info),
                    )
                except Exception as exc:  # noqa: BLE001 - the reason belongs on the run
                    failures.append(f"{name}: {type(exc).__name__}: {exc}")
                    continue
                written += 1
                if doc_id is not None:
                    self._index.enqueue(doc_id, run.collection_id)
        self._record(payload.run_id, requester, payload.batch_index, written + skipped, failures)
        if self._all_accounted(payload.run_id):
            self._enqueue_step(payload.run_id, "finalize", requester)

    def _finalize(self, run_id: str, requester: str) -> None:
        """Restore the cards and release the archive — exactly once.

        The gate is CLAIMED before the work, not after: two batches can both see the
        last slot close, and a broker can redeliver. `restore_cards` snapshots the
        collection before writing, so two finalizers would each see an empty
        candidate set and both create — the duplicate-card defect #701 closed.
        """
        run = self._read(run_id)
        if run is None or run.finished:
            # A redelivery after the winner finished: the archive it would need is
            # already released, so reading it would fail and the job would be retried
            # forever over work that is done. The claim below still handles the
            # CONCURRENT case, where neither finisher sees `finished` yet.
            return
        zip_data = self._archive_of(run)  # read BEFORE the claim clears it
        claimed = False

        def claim(current: ImportRun) -> ImportRun | None:
            nonlocal claimed
            # Reset per ATTEMPT. The CAS retries on a conflict, and a flag left set
            # by the losing attempt would tell the loser it had won: it aborts the
            # write correctly and then restores the cards anyway.
            claimed = False
            if current.finished:
                return None  # someone else already won the gate
            claimed = True
            return msgspec.structs.replace(current, finished=True, archive=None)

        self._cas(run_id, requester, claim)
        if not claimed:
            return
        restore_cards(
            self._spec,
            run.collection_id,
            _manifest_of(zip_data).get("context_cards", []),
            run.mode,
        )

    # ── run bookkeeping ──────────────────────────────────────────────
    def _cas(
        self, run_id: str, requester: str, mutate: Callable[[ImportRun], ImportRun | None]
    ) -> ImportRun | None:
        """Optimistic read-modify-write on the run. ``mutate`` returns the next run,
        or ``None`` to abort with no write (an idempotent no-op).

        `process` jobs carry ``partition_key=None`` so they parallelise on purpose,
        and the partition check the queue backends do is documented as best-effort —
        so a plain read-modify-write loses updates. A lost update here is not a
        miscount: the finalize gate counts slots, so one dropped slot means finalize
        never fires, the cards are never restored, the staged archive is never
        released, and the caller polls a finished import forever. Copied from
        ``CardGenRunStore._cas``, which solves the same problem one file over.

        The write acts AS the requester: the run is owner-only, so a worker writing
        as itself is a stranger to it, and a rule phrased around identity would then
        have to name the worker — the shape ``GraphMirrorChecker`` warns about.
        """
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = self._run_rm.get(run_id)
            except ResourceNotFoundError:
                return None  # cascaded away (collection deleted mid-flight)
            run = res.data
            if not isinstance(run, ImportRun):  # pragma: no cover - defensive
                return None
            new = mutate(run)
            if new is None:
                return None
            try:
                with self._run_rm.using(user=requester):
                    self._run_rm.modify(
                        run_id,
                        new,
                        status=RevisionStatus.draft,
                        expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                    )
                return new
            except PreconditionFailedError:
                continue  # another writer won the race — re-read and retry
        raise RuntimeError(f"ImportRun CAS exhausted retries for {run_id}")  # pragma: no cover

    def _patch(self, run_id: str, requester: str, **fields: object) -> None:
        self._cas(run_id, requester, lambda run: msgspec.structs.replace(run, **fields))

    def _record(
        self, run_id: str, requester: str, batch_index: int, written: int, failures: list[str]
    ) -> None:
        """Close one batch's slot in the join AND add its per-document outcome.

        Two different readers: the finalize gate counts BATCHES (it only needs to know
        every slot is closed), while a caller wants documents — how many landed, and
        the path plus reason for each that did not."""

        def mutate(run: ImportRun) -> ImportRun | None:
            if batch_index in run.done or batch_index in run.failed:
                return None  # idempotent: a redelivered batch must not double-count
            slot = "failed" if failures and not written else "done"
            return msgspec.structs.replace(
                run,
                done=[*run.done, batch_index] if slot == "done" else run.done,
                failed=[*run.failed, batch_index] if slot == "failed" else run.failed,
                written=run.written + written,
                # Capped: see MAX_ERROR_LINES. `written` and `members` still give
                # the true totals, so the cap costs detail, never the verdict.
                errors=[*run.errors, *failures][:MAX_ERROR_LINES],
            )

        self._cas(run_id, requester, mutate)

    def _all_accounted(self, run_id: str) -> bool:
        run = self._read(run_id)
        return run is not None and len(run.done) + len(run.failed) >= run.total

    def _enqueue_step(
        self,
        run_id: str,
        kind: str,
        requester: str,
        *,
        member_start: int = 0,
        member_end: int = 0,
        batch_index: int = 0,
        partition_key: str | None = "",
    ) -> None:
        """Queue the next step AS the requester, so every step of one import is
        credited to the person who asked for it rather than to the worker."""
        key = run_id if partition_key == "" else partition_key
        with self._job_rm.using(user=requester):
            self._job_rm.create(
                ImportJob(
                    payload=ImportPayload(
                        run_id=run_id,
                        kind=kind,
                        member_start=member_start,
                        member_end=member_end,
                        batch_index=batch_index,
                    ),
                    partition_key=key,
                )
            )

    # ── lifecycle ────────────────────────────────────────────────────
    def _ensure_consuming(self) -> None:
        if not self._consuming:
            self._consuming = True
            self._job_rm.start_consume(block=False)

    def start_consuming(self) -> None:
        """Start this process's background consumer once (idempotent). ``create_app``
        calls it at startup so idle pods help drain the queue."""
        self._ensure_consuming()

    @property
    def consuming(self) -> bool:
        """Whether the background consumer is running (#312) — observable so the
        API's ``run_consumers`` gate can be asserted and a worker can report it is
        draining its JobType."""
        return self._consuming

    def _stop_consuming(self) -> None:
        self._consuming = False
        self._job_rm.message_queue.stop_consuming()  # ty: ignore[unresolved-attribute]

    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    async def aclose(self) -> None:
        """Await every in-flight step (graceful shutdown / test sync) by polling until
        the queue drains. Starts the consumer first so a coordinator that never called
        ``start_consuming`` still flushes, and stops it once drained so no idle daemon
        thread is left behind."""
        if self._active_count() == 0 and not self._consuming:
            return
        self._ensure_consuming()
        while self._active_count() != 0:
            await asyncio.sleep(_DRAIN_INTERVAL)
        self._stop_consuming()
