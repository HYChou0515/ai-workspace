"""Asking about jobs: `jobs`, `queue`, `files`, `fetch`, `logs`, `cancel`.

`jobs` is the one call a person's "is it done yet?" needs (D2): every job this
workspace sent, each with where it is now. The others look at one job.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

from job_contract import JobBackend, JobError, NotFound, Retryable, Status

from job_tool import records
from job_tool import run as _run
from job_tool.commands import JOBS_PAGE, LOG_PAGE
from job_tool.run import Deadline, Outcome, TimedOut, status_dict, within

#: Entries one `files` answer lists at most. Tool output is capped by the
#: platform, so an unbounded listing would come back cut off — and a cut-off
#: listing reads as "this is everything".
FILES_MAX = 200

_FINISHED_NEXT = (
    "For a finished job: files lists what it left, fetch copies any of it into the "
    "workspace, logs reads its log."
)
_RUNNING_NEXT = (
    "Unfinished jobs run on their own and nothing announces when they end: tell the user "
    "to ask again later."
)


def _when(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds))


def _inside(path: str, what: str) -> str:
    """`path`, normalised, when it names something inside a job's folder."""
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        raise Retryable(f"{what} {path!r} must be a path inside the job's folder, without '..'")
    return pure.as_posix()


# ── jobs ─────────────────────────────────────────────────────────────────────


def _ask_status(backend: type[JobBackend], job_id: str) -> Status:
    try:
        return within(backend.budget.status, lambda: backend().status(job_id))
    except NotFound:
        return Status("unknown", state="the backend no longer has this job")
    except (TimedOut, JobError) as exc:
        return Status("unknown", state=str(exc))


def _jobs(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    deadline = Deadline()
    limit = args.get("limit", JOBS_PAGE)
    every = list(reversed(records.every()))
    shown, more = every[:limit], max(0, len(every) - limit)
    listed: list[dict[str, Any]] = []
    any_finished = any_running = False

    for record in shown:
        entry: dict[str, Any] = {
            "job_id": record.job_id,
            "workdir": record.workdir,
            "resources": record.resources,
            "sent_at": _when(record.created_at),
            "state": "started" if record.started else "uploading",
            "status": None,
        }
        if not record.started:
            arguments = json.dumps({"workdir": record.workdir, "resources": record.resources})
            entry["next"] = (
                f"Not all of it was sent; call run with {arguments} to finish sending and start it."
            )
        elif record.last is not None and Status(**record.last).finished:
            entry["status"] = record.last  # finished: nothing about it changes again
        elif deadline.left() < backend.budget.status:
            entry["next"] = "Its status was not checked this time; call jobs again for it."
        else:
            status = _ask_status(backend, record.job_id)
            entry["status"] = status_dict(status)
            record.last = status_dict(status)
            records.save(record)

        if entry["status"] is not None:
            if Status(**entry["status"]).finished:
                any_finished = True
            else:
                any_running = True
        listed.append(entry)

    if not listed:
        advice = "No job was sent from this workspace; run sends one."
    else:
        advice = (
            " ".join(
                text
                for text, wanted in ((_FINISHED_NEXT, any_finished), (_RUNNING_NEXT, any_running))
                if wanted
            )
            or "Call jobs again for the ones not checked."
        )
    if more:
        advice += f" {more} older jobs are not shown; raise limit to see them."
    return Outcome(0, {"jobs": listed, "more": more, "next": advice})


# ── queue ────────────────────────────────────────────────────────────────────


def _queue(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    capacity = within(backend.budget.capacity, lambda: backend().capacity())
    ours = {record.job_id for record in records.every()}
    return Outcome(
        0,
        {
            "workers": [asdict(worker) for worker in capacity.workers],
            "queue": [{**asdict(job), "mine": job.job_id in ours} for job in capacity.queue],
            "quota": capacity.quota,
            "next": (
                "Asking for what a free worker has starts sooner; a job's place in the queue "
                "is in jobs."
            ),
        },
    )


# ── files ────────────────────────────────────────────────────────────────────


def _files(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    job_id = args["job_id"]
    path = _inside(args.get("path", "."), "path")
    entries = within(backend.budget.list_files, lambda: backend().list_files(job_id, path))
    entries = sorted(entries, key=lambda e: e.path)
    shown, more = entries[:FILES_MAX], max(0, len(entries) - FILES_MAX)
    advice = "fetch copies a file or a folder into the workspace."
    if more:
        advice += f" {more} more are not listed; give a narrower path to see them."
    return Outcome(
        0,
        {
            "job_id": job_id,
            "entries": [asdict(entry) for entry in shown],
            "more": more,
            "next": advice,
        },
    )


# ── fetch ────────────────────────────────────────────────────────────────────


def _destination(dest: str, workspace: Path) -> Path:
    """Where to write, inside the workspace. Checked by name AND where it
    really leads, since a link in the workspace can point anywhere; and it
    must name something below the workspace, not the workspace itself."""
    pure = PurePosixPath(dest)
    try:
        lands = (workspace / pure).resolve()
    except (OSError, ValueError) as exc:  # a NUL in the path
        raise Retryable(f"dest {dest!r} is not a usable path: {exc}") from exc
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or not lands.is_relative_to(workspace)
        or lands == workspace
    ):
        raise Retryable(
            f"dest {dest!r} must name a path inside the workspace: relative, without '..', "
            "not leading outside it through a link"
        )
    return lands


def _fetch_one(
    backend: type[JobBackend],
    job_id: str,
    remote: str,
    size: int,
    target: Path,
    deadline: Deadline,
) -> bool:
    """Copy one file, resuming from its `.part`. Whether it is now whole."""
    if target.is_file() and target.stat().st_size == size:
        return True
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    budget = backend.budget.download
    with part.open("ab") as stream:
        while have < size:
            if deadline.left() < budget:
                return False
            data = within(
                budget,
                lambda o=have: backend().download(job_id, remote, o, min(_run.PIECE, size - o)),
            )
            if not data:
                part.unlink(missing_ok=True)
                raise JobError(
                    f"{remote} ended at {have} bytes on the other side, but was listed as "
                    f"{size}: it may have changed while it was copied; fetch it again"
                )
            stream.write(data)
            have += len(data)
    os.replace(part, target)
    return True


def _fetch(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    deadline = Deadline()
    workspace = Path.cwd().resolve()
    job_id = args["job_id"]
    path = _inside(args["path"], "path")
    dest = args.get("dest") or f"{records.FOLDER}/{job_id}/fetched/{path}"
    base = _destination(dest, workspace)

    entries = within(backend.budget.list_files, lambda: backend().list_files(job_id, path))
    exact = [e for e in entries if e.path == path and not e.is_dir]
    if exact:
        wanted = [(exact[0], base)]
    else:
        prefix = path.rstrip("/") + "/" if path != "." else ""
        wanted = [
            (e, base / PurePosixPath(e.path).relative_to(prefix or "."))
            for e in entries
            if not e.is_dir and e.path.startswith(prefix)
        ]
    if not wanted:
        raise NotFound(f"{job_id} has nothing at {path!r}; files lists what it has")

    saved: list[str] = []
    for entry, target in wanted:
        if not _fetch_one(backend, job_id, entry.path, entry.size, target, deadline):
            left = sum(
                e.size for e, t in wanted if not (t.is_file() and t.stat().st_size == e.size)
            )
            return Outcome(
                0,
                {
                    "job_id": job_id,
                    "state": "partial",
                    "saved": saved,
                    "bytes_left": left,
                    "next": "Not all of it arrived yet; call fetch again with the same "
                    "arguments to continue where it stopped.",
                },
            )
        saved.append(target.relative_to(workspace).as_posix())
    return Outcome(
        0,
        {"job_id": job_id, "state": "done", "saved": saved, "next": "Saved in the workspace."},
    )


# ── logs ─────────────────────────────────────────────────────────────────────


def _logs(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    job_id = args["job_id"]
    limit = args.get("limit", LOG_PAGE)
    budget = backend.budget.logs
    offset = args.get("offset")
    if offset is None:
        # The end is what a person asking "why did it fail?" needs.
        _, total = within(budget, lambda: backend().logs(job_id, 0, 1))
        offset = max(0, total - limit)
    text, total = within(budget, lambda: backend().logs(job_id, offset, limit))
    end = offset + len(text)
    parts = []
    if offset > 0:
        parts.append(f"{offset} earlier characters are not shown (offset=0 reads from the start).")
    parts.append(
        f"{total - end} more characters; call logs with offset={end} to read on."
        if end < total
        else "That is the end of the log so far."
    )
    return Outcome(
        0,
        {"text": text, "offset": offset, "end": end, "total": total, "next": " ".join(parts)},
    )


# ── cancel ───────────────────────────────────────────────────────────────────


def _cancel(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    job_id = args["job_id"]
    within(backend.budget.cancel, lambda: backend().cancel(job_id))
    status = _ask_status(backend, job_id)
    record = records.load(job_id)
    if record is not None:
        record.last = status_dict(status)
        records.save(record)
    return Outcome(
        0,
        {
            "job_id": job_id,
            "status": status_dict(status),
            "next": "Cancel accepted; jobs shows where it is from now on.",
        },
    )


COMMANDS: dict[str, Callable[[type[JobBackend], dict[str, Any]], Outcome]] = {
    "jobs": _jobs,
    "queue": _queue,
    "files": _files,
    "fetch": _fetch,
    "logs": _logs,
    "cancel": _cancel,
}
