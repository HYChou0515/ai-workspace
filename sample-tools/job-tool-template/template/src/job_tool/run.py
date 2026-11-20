"""Running one command: the budgets, the exit code, and what the model is told.

Every call to the backend runs in a daemon thread that is waited on for the
call's budget and no longer. A call that overruns is abandoned: the tool
answers, and `cli.entry` then ends the process without waiting for anything
the backend left running (a thread pool's workers, which the interpreter would
otherwise join at exit). A command that makes many calls — `run` uploading a
large folder, `fetch` downloading one — also watches the whole command's
deadline, and stops while there is still time to say how far it got.

The model hears two things: the answer on stdout (JSON, with a `next` line
saying what to do now) and the exit code. The platform adds its own advice for
2 (calling again may work) and 3 (a person must act); 0 is an answer and 1 is a
failure described by its message alone.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from job_contract import MARGIN, PLATFORM_CEILING, JobBackend, JobError, Status

#: How long one command may take in all. The platform stops a tool at its
#: ceiling; the margin is the time left to say what happened.
CALL_SECONDS = PLATFORM_CEILING - MARGIN

#: Set by the platform on every dispatch: the item this call runs for.
ITEM_ENV = "WORKSPACE_ITEM_ID"


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


class Deadline:
    """The end of this command, read once when it starts."""

    def __init__(self) -> None:
        self._end = time.monotonic() + CALL_SECONDS

    def left(self) -> float:
        return self._end - time.monotonic()


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


def this_item() -> str | None:
    """The item this call runs for, or None — the MCP runner has none."""
    return os.environ.get(ITEM_ENV) or None


def status_dict(status: Status) -> dict[str, Any]:
    return asdict(status)


def run(name: str, args: dict[str, Any], backend: type[JobBackend]) -> Outcome:
    """Run command `name` with already-validated `args`."""
    from job_tool.ask import COMMANDS as ASKING
    from job_tool.send import send

    runners: dict[str, Callable[[type[JobBackend], dict[str, Any]], Outcome]] = {
        "run": send,
        **ASKING,
    }
    _abandoned.clear()  # the state of THIS call, not of an earlier one in the same process
    try:
        return runners[name](backend, args)
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
