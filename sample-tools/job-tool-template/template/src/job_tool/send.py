"""`run`: send a folder of the workspace to the other machine and start it.

One command, called as many times as it takes (docs/plan-job-queue.md D6).
Each call packs the folder, works out which job it is, asks the backend how
much of it has arrived, sends what is missing until time runs short, and —
once the backend holds every file whole — starts it.
"""

from __future__ import annotations

import time
from pathlib import Path, PurePosixPath
from typing import Any

from job_contract import JobBackend, JobError, NeedsAction, NotFound, Retryable, Status

from job_tool import records
from job_tool.pack import Packed, PackedFile, job_hash, pack
from job_tool import run as _run
from job_tool.run import Deadline, Outcome, TimedOut, status_dict, this_item, within

_STARTED = (
    "Started. It runs on its own and nothing announces when it ends: tell the user to "
    "ask about it later, and call jobs then."
)
_UPLOADING = (
    "Sent about {sent} of {total} bytes. Call run again with the same workdir and "
    "resources to continue; the job starts once everything has arrived."
)
_ALREADY = (
    "This {where} already sent the same folder with the same resources, and that job "
    "{how}: this is that job, not a new one. Call jobs to follow it; after it finishes, "
    "the same request starts a new one."
)
_UNFINISHED = "has not finished"
_UNREAD = (
    "may not have finished — its status could not be read just now, so it is not sent a second time"
)


def _folder(workdir: str, workspace: Path) -> Path:
    """The folder to send, or `Retryable` saying why it cannot be: a relative
    path inside the workspace (also where links lead), a folder, and not the
    records."""
    pure = PurePosixPath(workdir)
    root = (workspace / pure).resolve()
    records_folder = (workspace / records.FOLDER).resolve()
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or not root.is_relative_to(workspace)
        or root == records_folder
        or root.is_relative_to(records_folder)
    ):
        raise Retryable(
            f"workdir {workdir!r} must be a folder inside the workspace: a relative path, "
            f"without '..', not leading out through a link, and not {records.FOLDER}/ "
            "(where the tool keeps its records)"
        )
    if not root.is_dir():
        raise Retryable(f"workdir {workdir!r} is not a folder in the workspace")
    return root


def _arrived(backend: type[JobBackend], job_id: str) -> dict[str, int]:
    """What the other side holds of this job, file by file (D14): never what
    this tool believes it sent."""
    try:
        entries = within(backend.budget.list_files, lambda: backend().list_files(job_id, "."))
    except NotFound:
        return {}
    return {e.path: e.size for e in entries if not e.is_dir}


def _status_now(backend: type[JobBackend], job_id: str) -> Status | None:
    """The job's status; None when the backend has no such job; `unknown` when
    it cannot say — which must never be mistaken for "gone"."""
    try:
        return within(backend.budget.status, lambda: backend().status(job_id))
    except NotFound:
        return None
    except (TimedOut, JobError) as exc:
        return Status("unknown", state=str(exc))


#: How many ids past a lost record `_record_for` tries before giving up.
_ID_TRIES = 50


def _need(deadline: Deadline, seconds: float) -> None:
    """Stop before a call that could not finish inside this command."""
    if deadline.left() < seconds:
        raise Retryable(
            "This call ran out of time before it could finish. Call run again with the "
            "same arguments: nothing is lost, it carries on where it stopped."
        )


def _taken(backend: type[JobBackend], job_id: str) -> bool:
    """Whether the backend already started this id. Ids are counted from the
    workspace's records; if those were deleted, the next id may be an old
    job's — and starting it again would be a no-op pointing at old results."""
    try:
        status = within(backend.budget.status, lambda: backend().status(job_id))
    except NotFound:
        return False
    if status.phase == "unknown":
        raise Retryable(
            "Whether this job's id is free cannot be read from the backend just now; "
            "call run again shortly."
        )
    return True


def _record_for(
    backend: type[JobBackend],
    prior: records.Record | None,
    *,
    digest: str,
    workdir: str,
    resources: dict[str, Any],
    packed: Packed,
    item_id: str | None,
    deadline: Deadline,
) -> records.Record:
    if prior is not None and not prior.started:
        if prior.replaced_by is not None:  # the folder is back as it was
            prior.replaced_by = None
            records.save(prior)
        return prior  # an upload still owed: carry on with the same job
    attempt = prior.attempt + 1 if prior is not None else 1
    for _ in range(_ID_TRIES):
        _need(deadline, backend.budget.status)
        if not _taken(backend, f"j{digest[:16]}-{attempt}"):
            break
        attempt += 1
    else:
        raise JobError(
            f"the backend already has jobs j{digest[:16]}-1 to -{attempt}; this workspace's "
            f"{records.FOLDER}/ records do not know them"
        )
    record = records.Record(
        job_id=f"j{digest[:16]}-{attempt}",
        hash=digest,
        attempt=attempt,
        workdir=workdir,
        resources=resources,
        item_id=item_id,
        files=len(packed.files),
        bytes=packed.total,
        created_at=time.time(),
    )
    records.save(record)
    # A send of this folder that never finished will never start now: the folder
    # changed, and this is its new job (D11). Say so where jobs reads it.
    for stale in records.every():
        if (
            not stale.started
            and stale.replaced_by is None
            and stale.job_id != record.job_id
            and (stale.workdir, stale.resources, stale.item_id) == (workdir, resources, item_id)
        ):
            stale.replaced_by = record.job_id
            records.save(stale)
    return record


def _upload(
    backend: type[JobBackend],
    job_id: str,
    root: Path,
    owed: list[tuple[PackedFile, int | None]],
    deadline: Deadline,
) -> tuple[bool, int]:
    """Send what is owed, piece by piece, while there is time for a piece.
    Whether everything owed was sent, and how many bytes this call sent."""
    budget = backend.budget.upload
    sent = 0
    for packed_file, have in owed:
        offset = have or 0
        # Unbuffered: what is sent is what the file holds at that moment, so a
        # file cut short mid-send is seen, not served from an old buffer.
        with (root / packed_file.path).open("rb", buffering=0) as stream:
            stream.seek(offset)
            # An empty file the other side lacks still needs one (empty) piece.
            first = have is None
            while offset < packed_file.size or first:
                if deadline.left() < budget:
                    return False, sent
                data = stream.read(_run.PIECE)
                if not data and offset < packed_file.size:
                    raise Retryable(
                        f"{packed_file.path} changed while it was being sent. Call run "
                        "again: the folder as it is now is sent as a new job."
                    )
                within(
                    budget,
                    lambda d=data, o=offset: backend().upload(job_id, packed_file.path, o, d),
                )
                offset += len(data)
                sent += len(data)
                first = False
    return True, sent


def _owed(packed: Packed, arrived: dict[str, int]) -> list[tuple[PackedFile, int | None]]:
    owed = []
    for packed_file in packed.files:
        have = arrived.get(packed_file.path)
        if have is not None and have > packed_file.size:
            raise JobError(
                f"the other side holds {have} bytes of {packed_file.path}, more than the "
                f"{packed_file.size} it has here: what is there is not this job's folder"
            )
        if have != packed_file.size:
            owed.append((packed_file, have))
    return owed


def _progress(record: records.Record, packed: Packed, arrived_bytes: int) -> Outcome:
    return Outcome(
        0,
        {
            "job_id": record.job_id,
            "state": "uploading",
            "uploaded": arrived_bytes,
            "total": packed.total,
            "files": len(packed.files),
            "not_sent": list(packed.skipped),
            "next": _UPLOADING.format(sent=arrived_bytes, total=packed.total),
        },
    )


def _start(backend: type[JobBackend], record: records.Record) -> None:
    """Start it; when the answer does not come in time, ask whether it landed
    before saying anything. Starting the same id again is a no-op, so "call
    again" is always safe — but "started" is said only when it is true."""
    try:
        within(backend.budget.start, lambda: backend().start(record.job_id, record.resources))
    except TimedOut as exc:
        try:
            landed = within(backend.budget.status, lambda: backend().status(record.job_id))
        except NeedsAction:
            raise  # a person must act first: exit 3, in the backend's own words
        except NotFound:
            raise Retryable(
                f"The start gave no answer within {exc.seconds:g}s and the job is not there. "
                "Calling run again is safe: it starts this job, never a second one."
            ) from None
        except (TimedOut, JobError):
            landed = Status("unknown")
        if landed.phase == "unknown":
            # The backend's "cannot say" is not "it landed".
            raise Retryable(
                f"The start gave no answer within {exc.seconds:g}s and whether it landed "
                "cannot be read just now. Calling run again is safe: it starts this job, "
                "never a second one."
            ) from None
    record.started = True
    record.started_at = time.time()
    records.save(record)


def send(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    deadline = Deadline()
    workspace = Path.cwd().resolve()
    workdir: str = args["workdir"]
    resources: dict[str, Any] = args["resources"]
    root = _folder(workdir, workspace)
    try:
        packed = pack(root, never=workspace / records.FOLDER)
    except OSError as exc:
        # The workspace's file, not the backend's failure.
        raise Retryable(
            f"cannot read {exc.filename or workdir!r} in the workspace ({exc.strerror}); "
            "fix its permissions or remove it, then call run again"
        ) from exc
    if not packed.files:
        raise Retryable(
            f"workdir {workdir!r} has nothing to send: no files, once environments, caches "
            "and what its .gitignore names are left out"
        )
    item_id = this_item()
    digest = job_hash(packed, resources, item_id)

    prior = records.latest_for(digest)
    if prior is not None and prior.started:
        _need(deadline, backend.budget.status)
        status = _status_now(backend, prior.job_id)
        if status is not None and not status.finished:
            prior.last = status_dict(status)
            records.save(prior)
            where = "item" if item_id is not None else "workspace"
            how = _UNREAD if status.phase == "unknown" else _UNFINISHED
            return Outcome(
                0,
                {
                    "job_id": prior.job_id,
                    "state": "already",
                    "status": status_dict(status),
                    "next": _ALREADY.format(where=where, how=how),
                },
            )

    record = _record_for(
        backend,
        prior, digest=digest, workdir=workdir, resources=resources, packed=packed,
        item_id=item_id, deadline=deadline,
    )  # fmt: skip

    _need(deadline, backend.budget.list_files)
    arrived = _arrived(backend, record.job_id)
    owed = _owed(packed, arrived)
    held = sum(min(arrived.get(f.path, 0), f.size) for f in packed.files)
    complete, sent = _upload(backend, record.job_id, root, owed, deadline)

    budget = backend.budget
    if not complete or deadline.left() < budget.list_files + budget.start + budget.status:
        return _progress(record, packed, held + sent)

    # Start only on the other side's word that every file is whole (D14).
    arrived = _arrived(backend, record.job_id)
    if _owed(packed, arrived):
        held = sum(min(arrived.get(f.path, 0), f.size) for f in packed.files)
        return _progress(record, packed, held)

    _start(backend, record)
    return Outcome(
        0,
        {
            "job_id": record.job_id,
            "state": "started",
            "files": len(packed.files),
            "total": packed.total,
            "not_sent": list(packed.skipped),
            "next": _STARTED,
        },
    )
