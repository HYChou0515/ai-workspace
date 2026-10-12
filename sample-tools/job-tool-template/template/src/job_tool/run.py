"""Running one call: the budget, the exit code, and what the model is told.

Every call to the backend runs in a daemon thread that is waited on for the
call's budget and no longer. A call that overruns is abandoned: the tool
answers, and `cli.entry` then ends the process without waiting for anything
the backend left running (a thread pool's workers, which the interpreter would
otherwise join at exit). So a backend that hangs costs the time its budget
allows — for `submit`, its budget plus `find_by_key`'s — never the platform's
whole ceiling.

The model hears two things: the answer on stdout (JSON, with a `next` line
saying what to do now) and the exit code. The platform adds its own advice for
2 (calling again may work) and 3 (a person must act); 0 is an answer and 1 is a
failure described by its message alone.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from job_contract import JobBackend, JobError, JobStatus, NeedsAction, Retryable, Submitted

from job_tool.commands import LIST_PAGE, LOG_PAGE


@dataclass(frozen=True)
class Outcome:
    exit_code: int
    answer: dict[str, Any] | None = None
    """Printed on stdout as JSON. None on failure."""
    error: str | None = None
    """Printed on stderr. None on success."""


class TimedOut(Exception):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"no answer within {seconds:g}s")
        self.seconds = seconds


_abandoned = threading.Event()


def abandoned() -> bool:
    """Whether a call was given up on, so something may still be running that
    must not be waited for when the process ends."""
    return _abandoned.is_set()


def within[T](seconds: float, fn: Callable[[], T]) -> T:
    """`fn()`, or `TimedOut` once `seconds` pass without it returning."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # handed to the caller below, whatever it is
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        _abandoned.set()
        raise TimedOut(seconds)
    if "error" in box:
        raise box["error"]
    return box["value"]


#: Set by the platform on every dispatch: the item this call runs for (D44).
ITEM_ENV = "WORKSPACE_ITEM_ID"


def describe_window(seconds: float) -> str:
    """`window_seconds` as a person says it: "24 hours", "10 minutes", "90 seconds"."""
    for size, unit in ((3600, "hour"), (60, "minute")):
        if seconds >= size and seconds % size == 0:
            count = int(seconds // size)
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return f"{seconds:g} second{'' if seconds == 1 else 's'}"


def this_item() -> str | None:
    """The item this call runs for, or None — the MCP runner has none (D47)."""
    return os.environ.get(ITEM_ENV) or None


def _job(job: JobStatus) -> dict[str, Any]:
    """A job as the model reads it: the backend's record, plus whether it came
    from this item (D42) — the queue is shown whole, so this is how the model
    tells "yours" from "someone else's"."""
    here = this_item()
    return {**asdict(job), "from_this_item": here is not None and job.item_id == here}


def _status(job: JobStatus, advice: str) -> dict[str, Any]:
    return {"job": _job(job), "next": advice}


_RUNNING = "Still running; it continues on its own. Call get again later to check."
_UNKNOWN = (
    "The backend could not say just now. That does NOT mean the job does not exist; "
    "call get again shortly."
)


def _done(backend: type[JobBackend]) -> str:
    # `done` covers success, failure and cancel alike — the shell reads only
    # the phase — so outputs are offered as a possibility, never promised.
    offered = backend.offers()
    then = [
        hint
        for call, hint in (
            ("logs", "logs reads its log"),
            ("fetch", "fetch saves its outputs, if it produced any"),
        )
        if call in offered
    ]
    ended = "Finished; its state says how it ended."
    return ended + (f" If useful: {'; '.join(then)}." if then else "")


def _advice(job: JobStatus, backend: type[JobBackend]) -> str:
    """What to do next about `job`, decided by its phase alone."""
    if job.phase == "unknown":
        return _UNKNOWN
    return _RUNNING if job.phase == "running" else _done(backend)


def _reported(job: JobStatus, backend: type[JobBackend]) -> Outcome:
    """A status, with the advice its phase calls for. `unknown` is exit 2:
    asking again may get an answer."""
    return Outcome(2 if job.phase == "unknown" else 0, _status(job, _advice(job, backend)))


def _get(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    budget = backend.contract.budget.get
    return _reported(within(budget, lambda: backend().get(args["job_id"])), backend)


_SUBMITTED = (
    "Submitted; it runs on its own and nothing will announce when it ends. "
    "Tell the user to ask again later, and call get with this job_id then."
)
_ALREADY = (
    "{who} already submitted the same work within the last {window}: this is that job, "
    "not a new one, and it may have finished. Call get to see where it is."
)
_LANDED = (
    "The submit was slow to answer, but the job is there. It runs on its own; call get "
    "with this job_id to see where it is."
)


def _submit(backend: type[JobBackend], spec: dict[str, Any]) -> Outcome:
    """Submit, and when the answer does not come in time, find out whether it
    landed before saying anything — so "try again" is never said about a job
    that is already running (two jobs, two bills)."""
    contract = backend.contract
    item_id = this_item()
    key = contract.idempotency.key(spec, item_id=item_id)
    calling: list[bool] = []  # set the moment `submit` itself is entered

    def send() -> Submitted:
        instance = backend()
        calling.append(True)
        return instance.submit(spec, key, item_id)

    try:
        result = within(contract.budget.submit, send)
    except TimedOut:
        if not calling:
            # Stuck building the backend, before `submit` was called, and the
            # stuck thread ends with this process. Not airtight — the build
            # could finish in the instant before the process ends and the
            # thread call `submit` — but the same spec carries the same key,
            # so sending it again returns that job rather than starting a second.
            return Outcome(
                2,
                error=f"The backend gave no answer within {contract.budget.submit:g}s, "
                "before the job was sent, so it was most likely not sent. Submitting the "
                "same spec again is safe: it carries the same key.",
            )
        try:
            found = within(contract.budget.find_by_key, lambda: backend().find_by_key(key))
        except BaseException as exc:  # noqa: BLE001 - TimedOut, sys.exit, anything: unknown
            # Still unknown. A lookup that needs a person keeps exit 3, which is
            # what lets the platform offer that person a fix; anything else is
            # 1 — never 2, whose "call it again" would contradict "list first".
            return Outcome(
                NeedsAction.exit_code if isinstance(exc, NeedsAction) else 1,
                error=f"The submit's outcome is unknown: the backend did not confirm it within "
                f"{contract.budget.submit:g}s, and could not be asked whether it arrived "
                f"({type(exc).__name__}: {exc}). Before submitting again, call list to see "
                "whether it is there.",
            )
        if found is None:
            return Outcome(
                2,
                error=f"The backend did not confirm the submit within "
                f"{contract.budget.submit:g}s, and has no job for it. Submitting the same "
                "spec again is safe: it carries the same key, so a late arrival of the "
                "first one is returned rather than started twice.",
            )
        # Whether this call started it, the lookup cannot say: neither claim it.
        return Outcome(
            0, {"job_id": found.job_id, "created": None, "job": _job(found), "next": _LANDED}
        )
    if not isinstance(result, Submitted):
        # Guessing "new" would be exactly the false sentence D51 exists to stop.
        raise TypeError(
            f"submit must return Submitted(job_id, created), got {type(result).__name__}"
        )
    advice = (
        _SUBMITTED
        if result.created
        else _ALREADY.format(
            # D47: with no item there is no "this item" to speak of.
            who="This item" if item_id is not None else "The same work was",
            window=describe_window(contract.idempotency.window_seconds),
        )
    )
    return Outcome(0, {"job_id": result.job_id, "created": result.created, "next": advice})


def _list(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    cursor, limit = args.get("cursor"), args.get("limit", LIST_PAGE)
    jobs, after = within(backend.contract.budget.list, lambda: backend().list(cursor, limit))
    # The model passes it back as `cursor`, which the schema says is a string.
    after = None if after is None else str(after)
    advice = (
        f"More jobs are older than these; call list with cursor={after!r} for them."
        if after is not None
        else "There are no more jobs."
    )
    return Outcome(0, {"jobs": [_job(job) for job in jobs], "next_cursor": after, "next": advice})


def _update(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    budget = backend.contract.budget.update
    job = within(budget, lambda: backend().update(args["job_id"], args["patch"]))
    return Outcome(
        0,
        _status(
            job,
            "Change accepted; it may not have taken effect yet. Call get to see when it has.",
        ),
    )


def _cancel(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    budget = backend.contract.budget.cancel
    job = within(budget, lambda: backend().cancel(args["job_id"]))
    # The cancel itself succeeded; where the job now is decides what follows.
    return Outcome(0, _status(job, f"Cancel accepted. {_advice(job, backend)}"))


def _logs(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    job_id, offset = args["job_id"], args.get("offset", 0)
    limit = args.get("limit", LOG_PAGE)
    budget = backend.contract.budget.logs
    text, total = within(budget, lambda: backend().logs(job_id, offset, limit))
    end = offset + len(text)
    advice = (
        f"{total - end} more characters; call logs with offset={end} to read on."
        if end < total
        else "That is the end of the log so far."
    )
    return Outcome(0, {"text": text, "offset": offset, "end": end, "total": total, "next": advice})


def _fetch(backend: type[JobBackend], args: dict[str, Any]) -> Outcome:
    dest = PurePosixPath(args["dest"])
    # The working directory is the user's workspace; a path that leaves it
    # would write somewhere nobody sees, or somewhere nobody should. Checked
    # by name AND where it really leads, since a symlink in the workspace can
    # point anywhere; and it must name a file, not the workspace itself.
    try:
        workspace = Path.cwd().resolve()
        lands = (workspace / dest).resolve()
    except (OSError, ValueError) as exc:  # a NUL in the path, a vanished cwd
        raise Retryable(
            f"dest {args['dest']!r} must name a file inside the workspace; it is not a "
            f"usable path: {exc}"
        ) from exc
    if (
        dest.is_absolute()
        or ".." in dest.parts
        or not dest.parts
        or not lands.is_relative_to(workspace)
        or lands == workspace
    ):
        raise Retryable(
            f"dest {args['dest']!r} must name a file inside the workspace: a relative path, "
            "without '..', that does not lead outside it through a link"
        )
    budget = backend.contract.budget.fetch
    path = within(budget, lambda: backend().fetch(args["job_id"], args["name"], str(dest)))
    return Outcome(0, {"path": path, "next": f"Saved to {path} in the workspace."})


_RUNNERS: dict[str, Callable[[type[JobBackend], dict[str, Any]], Outcome]] = {
    "submit": _submit,
    "get": _get,
    "list": _list,
    "update": _update,
    "cancel": _cancel,
    "logs": _logs,
    "fetch": _fetch,
}


def run(name: str, args: dict[str, Any], backend: type[JobBackend]) -> Outcome:
    """Run command `name` with already-validated `args`."""
    _abandoned.clear()  # the state of THIS call, not of an earlier one in the same process
    try:
        return _RUNNERS[name](backend, args)
    except TimedOut as exc:
        return Outcome(
            2,
            error=f"The backend gave no answer within {exc.seconds:g}s; "
            "it may be busy. Calling again may work.",
        )
    except JobError as exc:
        return Outcome(exc.exit_code, error=str(exc))
    except BaseException as exc:  # noqa: BLE001 - raised by the backend's code, in its thread
        # The backend broke in a way it did not describe — including calling
        # sys.exit, which would otherwise end this process with exit 0 and no
        # answer, a failure dressed as success. Say what, plainly: a traceback
        # is noise to the model, and stdout must stay empty.
        return Outcome(1, error=f"the backend failed: {type(exc).__name__}: {exc}")
