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

**Where the archive lives.** A worker never holds the whole archive: ``split``,
``process`` and ``finalize`` stream the staged blob to a local temp file and let
``zipfile`` seek within it, so a batch costs a chunk of memory plus scratch disk
rather than the archive's full size once per concurrent job. That is #715's "spill
to a temp file and let the job read it".

**Known limitation — the UPLOAD side.** specstar's ``Binary`` takes ``bytes`` and
offers no streaming or file-handle input, so staging still costs one in-memory
copy in the API pod while the blob is written (specstar#447). The synchronous path
has the same peak (``await file.read()``), so this is not a regression — but the
ceiling on what a caller may push is still set by the API pod's RAM, and
pretending otherwise would leave the next person to discover it under load.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from typing import IO, TYPE_CHECKING, Annotated, Any

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

from ..perm.authorize import Actor, authorize
from ..resources.groups import groups_of
from ..resources.kb import Collection
from .collection_export import MANIFEST_PATH
from .collection_import import doc_exists, document_members, restore_cards
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

# What a caller sees when the run is refused. Deliberately the same line whether the
# collection is private, restricted or gone: a distinguishable message would turn the
# import queue into an existence oracle for collections the requester cannot read.
_DENIED = "import refused: you may not add content to this collection"

# Prefixes for the two failures that belong to no single document: the staged
# archive could not be opened at all, and the manifest's cards could not be
# restored after the documents already landed.
_UNREADABLE = "archive could not be read"
_CARDS_FAILED = "context cards were not restored"

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


def _members_of(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The archive's document members, ordered by PATH.

    Which members are documents is `document_members` — shared with the
    synchronous importer, because the two had the same predicate written out
    twice. What is added here is the ordering, and only this path needs it:
    ``split`` hands out batches as index RANGES into this list, so what a range
    means has to be a property of the archive's CONTENT rather than of however
    the zip happened to be written. Two archives holding the same documents then
    split into the same batches, which is what makes a `member_start` in an
    error line, or a comparison between two runs of the same content, mean
    anything.

    It is NOT what makes a retry safe — that comes from the staged blob being
    immutable, so `infolist()` would already return the same order twice. Saying
    otherwise would credit the sort with a guarantee it does not provide, and
    leave the real one undocumented.

    `sorted` is stable, so two entries sharing a name keep their archive order —
    which `mode="skip"` can observe (the first is written, the second skipped).
    """
    return sorted(
        (info for info, _ in document_members(zf)),
        key=lambda i: (i.filename, i.header_offset),
    )


def _manifest_of(zf: zipfile.ZipFile) -> dict[str, Any]:
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
        superusers: frozenset[str] = frozenset(),
    ) -> None:
        self._spec = spec
        self._ingestor = ingestor
        self._index = index_coordinator
        # Same set the HTTP routes gate on. The worker has to reach the same verdict
        # the request would have, so it needs the same inputs — see `_may_write`.
        self._superusers = superusers
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
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            member_count = len(_members_of(zf))
        with self._run_rm.using(user=user):
            run_id = self._run_rm.create(
                ImportRun(
                    collection_id=collection_id,
                    mode=mode,
                    members=member_count,
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

    @contextlib.contextmanager
    def _archive_file(self, run: ImportRun) -> Iterator[IO[bytes]]:
        """Materialise the staged archive as a seekable LOCAL file.

        #715 asked for the archive to be spilled to a temp file rather than held in
        memory, because otherwise the size a caller may push is decided by the
        worker's RAM. A job reads a slice of the zip, which needs random access —
        so the bytes land on local disk and `zipfile` seeks within them, instead of
        the whole archive sitting in the heap for the length of the batch.

        Streaming when the blob store offers it. `MemoryBlobStore` does not, so the
        fallback still restores the blob whole; that path is what unit tests take,
        and the streaming one is covered by a double that models a store which
        implements `get_stream` (both disk and S3 do). The fallback is not dead
        code waiting to rot — it is the only path when a store cannot stream.
        """
        with tempfile.TemporaryFile(suffix=".zip") as tmp:
            stream = None
            file_id = run.archive.file_id if run.archive is not None else None
            if isinstance(file_id, str) and file_id:
                stream = self._run_rm.get_blob_stream(file_id)
            if stream is not None:
                for chunk in stream.iterator:
                    tmp.write(chunk)
            else:
                restored = self._run_rm.restore_binary(run).archive
                assert restored is not None and isinstance(restored.data, bytes)
                tmp.write(restored.data)
            tmp.flush()
            tmp.seek(0)
            yield tmp

    # ── authorization ────────────────────────────────────────────────
    def _may_write(self, collection_id: str, requester: str) -> bool:
        """May ``requester`` add content to this collection, right now?

        The synchronous importer authorises inside the request, so the caller's
        identity and the write are one step apart. Moving the write to a worker
        splits them, and what survives the split is a FIELD — ``collection_id`` on a
        run whose owner may PATCH it. Trusting that field is trusting the requester
        to name their own target: stage an import, re-point it at a collection you
        cannot even read, and the worker performs the write with no one left to say
        no. ``restore_cards`` makes that worse than a stray file, because it matches
        by KEY and overwrites — and context cards are injected into agent prompts.

        So the worker re-derives the verdict the route would have reached, from the
        same primitive (`authorize`) with the same inputs (`created_by`, the
        superuser set, the requester's GROUPS — a `group:<id>` grant has to keep
        working here or the fix would quietly narrow who can import). Checked at
        each write STEP rather than once at the start: `collection_id` is mutable
        for as long as the run is, so a check at split is a check on a value that
        can still change underneath the batches.
        """
        rm = self._spec.get_resource_manager(Collection)
        try:
            coll = rm.get(collection_id).data
            created_by = rm.get_meta(collection_id).created_by
        except ResourceNotFoundError:
            return False  # deleted mid-run — nothing to write into
        assert isinstance(coll, Collection)
        return authorize(
            Actor.human(requester, groups=groups_of(self._spec, requester)),
            "add_content",
            coll.permission,  # None ≡ public, as everywhere else
            created_by=created_by,
            superusers=self._superusers,
        )

    def _patch_errors(self, run_id: str, requester: str, reason: str) -> None:
        """Add one line to the run's failure list, leaving everything else alone.

        `_record` closes a BATCH slot as well as adding lines, so it is the wrong
        tool for a failure that belongs to no batch."""

        def mutate(current: ImportRun) -> ImportRun | None:
            return msgspec.structs.replace(
                current, errors=[*current.errors, reason][:MAX_ERROR_LINES]
            )

        self._cas(run_id, requester, mutate)

    def _refuse(self, run_id: str, requester: str, reason: str) -> None:
        """End the run without writing, and say why on the row the caller polls.

        A refusal that only returned would leave the caller polling a run that never
        moves — the same silence #715 exists to remove. The archive is released with
        it: a retry would be refused identically, so keeping 200 MB staged buys
        nothing. `written` stays below `members`, which is the documented verdict for
        an incomplete import, and the reason lands in `errors` beside it.
        """

        def deny(current: ImportRun) -> ImportRun | None:
            if current.finished:
                return None
            return msgspec.structs.replace(
                current,
                finished=True,
                archive=None,
                errors=[*current.errors, reason][:MAX_ERROR_LINES],
            )

        self._cas(run_id, requester, deny)

    def _split(self, run_id: str, requester: str) -> None:
        """Seed the join state and fan the members out in batches."""
        run = self._read(run_id)
        if run is None or run.finished:
            return
        if not self._may_write(run.collection_id, requester):
            self._refuse(run_id, requester, _DENIED)
            return
        try:
            with self._archive_file(run) as fh, zipfile.ZipFile(fh) as zf:
                members = _members_of(zf)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the run
            # Nothing has been written yet, so this is terminal rather than partial.
            # Letting it propagate would retry until the job died and leave the run
            # at `finished=false, errors=[]` for ever — a caller polling a 202 that
            # never moves, which is the exact silence #715 exists to remove. A
            # visible failure they can act on beats an invisible retry they cannot.
            self._refuse(run_id, requester, f"{_UNREADABLE}: {type(exc).__name__}: {exc}")
            return
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
        if not self._may_write(run.collection_id, requester):
            self._refuse(payload.run_id, requester, _DENIED)
            return
        written = 0
        skipped = 0
        failures: list[str] = []
        with contextlib.ExitStack() as stack:
            try:
                # Entering is where the archive is actually fetched, so this is the
                # step that fails on a corrupt or vanished blob. The per-member
                # `try` below cannot cover it — that one is about ONE document.
                fh = stack.enter_context(self._archive_file(run))
                zf = stack.enter_context(zipfile.ZipFile(fh))
                # Inside the same guard as the open, because `split` guards it too:
                # leaving it out would make one step survive a malformed central
                # directory and the other wedge on it, for no stated reason.
                members = _members_of(zf)[payload.member_start : payload.member_end]
            except Exception as exc:  # noqa: BLE001 - the reason belongs on the run
                # Close this batch's slot with the reason instead of raising, or the
                # finalize gate never sees every slot closed: cards are never
                # restored, the archive is never released, and the caller polls a
                # run that is over in every sense except the one it reports.
                self._record(
                    payload.run_id,
                    requester,
                    payload.batch_index,
                    0,
                    [f"{_UNREADABLE}: {type(exc).__name__}: {exc}"],
                )
                if self._all_accounted(payload.run_id):
                    self._enqueue_step(payload.run_id, "finalize", requester)
                return
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
        if not self._may_write(run.collection_id, requester):
            # `restore_cards` writes by KEY into the collection, so finalize is a
            # write boundary in its own right — not a bookkeeping step that inherits
            # `_process`'s verdict.
            self._refuse(run_id, requester, _DENIED)
            return
        # Materialise BEFORE the claim: the claim releases the archive, so a
        # finalize that claimed first and read second would find nothing.
        with contextlib.ExitStack() as stack:
            try:
                fh = stack.enter_context(self._archive_file(run))
                zf = stack.enter_context(zipfile.ZipFile(fh))
            except Exception as exc:  # noqa: BLE001 - the reason belongs on the run
                # Every batch is already accounted for, so the documents are in and
                # the only thing left was the cards. Raising would retry a read that
                # will fail identically and leave the run at `finished=false` for
                # ever — the whole import invisible because its last step could not
                # open a file. `_refuse` is idempotent (it aborts on `finished`), so
                # two finalizers racing here still write once.
                self._refuse(run_id, requester, f"{_UNREADABLE}: {type(exc).__name__}: {exc}")
                return
            claimed = False

            def claim(current: ImportRun) -> ImportRun | None:
                nonlocal claimed
                # Reset per ATTEMPT. The CAS retries on a conflict, and a flag left
                # set by the losing attempt would tell the loser it had won: it
                # aborts the write correctly and then restores the cards anyway.
                claimed = False
                if current.finished:
                    return None  # someone else already won the gate
                claimed = True
                return msgspec.structs.replace(current, finished=True, archive=None)

            self._cas(run_id, requester, claim)
            if not claimed:
                return
            try:
                restore_cards(
                    self._spec,
                    run.collection_id,
                    _manifest_of(zf).get("context_cards", []),
                    run.mode,
                )
            except Exception as exc:  # noqa: BLE001 - the reason belongs on the run
                # The claim above already set `finished` and released the archive —
                # it has to, or two finalizers both restore (#701's duplicate cards).
                # So a raise here cannot be retried, and without this the run reports
                # `written == members`, `errors: []`, `finished: true` with NOT ONE
                # CARD restored: byte-identical to complete success. The documents
                # did land, so this is not a refusal — it is one more line in the
                # channel the per-document failures already use.
                self._patch_errors(
                    run_id, requester, f"{_CARDS_FAILED}: {type(exc).__name__}: {exc}"
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
