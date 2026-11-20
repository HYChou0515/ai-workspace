"""The values that cross the line between the tool and a backend.

A *job* is a folder of the workspace, sent to another machine and run there
(docs/plan-job-queue.md). The tool shell packs the folder, uploads it in
pieces, starts the job, and asks about it later; the backend only moves bytes
and answers. Everything here is what those calls take and return, and the
budget each may spend.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal, get_args

#: The platform stops a tool call after this many seconds (docs/tool-authoring.md §6).
PLATFORM_CEILING = 60.0
#: Kept back from every budget so the shell can still tell the model what happened.
MARGIN = 5.0


class ContractError(Exception):
    """A declaration that cannot be honoured. Raised as early as it can be
    known: a budget when it is written, a backend's missing call or bad
    declaration when it is loaded (`JobBackend.check`), a status or entry with
    an impossible value when the backend returns it."""


Phase = Literal["queued", "running", "succeeded", "failed", "unknown"]
PHASES: tuple[Phase, ...] = get_args(Phase)
#: The phases after which nothing about the job changes again.
FINISHED: tuple[Phase, ...] = ("succeeded", "failed")


@dataclass(frozen=True)
class Status:
    """One job as the backend sees it right now."""

    phase: Phase
    """`queued`: accepted, not running yet. `running`. `succeeded` / `failed`:
    finished — a cancelled job is `failed`, with `state` saying so.
    `unknown`: the backend could not say just now — never "does not exist";
    for that, raise `NotFound`."""
    state: str = ""
    """The backend's own word for where the job is ("OOM killed", "held").
    Shown to the model verbatim; the shell never interprets it."""
    position: int | None = None
    """Its place in the queue, 0 being next, when the backend knows it."""
    eta: str | None = None
    """When it is expected to start or end, in the backend's words."""
    worker: str | None = None
    """The machine it runs on, once it does."""

    def __post_init__(self) -> None:
        if self.phase not in PHASES:
            raise ContractError(
                f"phase={self.phase!r}: must be one of {list(PHASES)}; "
                "the backend's own word for it belongs in `state`"
            )
        if self.position is not None and (
            isinstance(self.position, bool)
            or not isinstance(self.position, int)
            or self.position < 0
        ):
            raise ContractError(f"position={self.position!r}: must be a whole number, 0 or more")

    @property
    def finished(self) -> bool:
        return self.phase in FINISHED


def _inside(path: str) -> bool:
    """Whether `path` names something under a job's folder."""
    parts = PurePosixPath(path).parts
    return bool(parts) and not PurePosixPath(path).is_absolute() and ".." not in parts


@dataclass(frozen=True)
class Entry:
    """One file or folder in a job's folder on the other machine."""

    path: str
    """Relative to the job's folder, with `/` between parts."""
    is_dir: bool
    size: int = 0
    """Bytes; 0 for a folder. The shell reads it to know how much of an
    upload has arrived, so it must be what is really there."""
    mtime: float | None = None
    """Seconds since the epoch, when the backend knows it."""

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not _inside(self.path):
            raise ContractError(
                f"path={self.path!r}: must be relative to the job's folder, without '..'"
            )
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise ContractError(f"size={self.size!r}: must be a whole number of bytes, 0 or more")


@dataclass(frozen=True)
class Worker:
    """One machine that runs jobs."""

    name: str
    state: str
    """The backend's word: "idle", "busy", "down", "draining"."""
    job_id: str | None = None
    """The job it runs now, if any."""
    free: dict[str, Any] = field(default_factory=dict)
    """What it has left, in the resource schema's terms ({"gpus": 2})."""


@dataclass(frozen=True)
class QueuedJob:
    """One job waiting for a machine."""

    job_id: str
    resources: dict[str, Any] = field(default_factory=dict)
    waiting_since: str | None = None


@dataclass(frozen=True)
class Capacity:
    """The machines, the queue, and what the credential may still use."""

    workers: tuple[Worker, ...] = ()
    queue: tuple[QueuedJob, ...] = ()
    """Waiting jobs, next first, as far as the credential may see them."""
    quota: dict[str, Any] | None = None
    """What the credential has used and may use ({"gpu_hours": "12 of 20"}),
    when the backend knows. The execution side enforces it; this only shows it."""


@dataclass(frozen=True)
class Budget:
    """How long each call may take, in seconds. The shell stops a call that
    runs past its budget and tells the model what that means for that call.

    `upload` and `download` are per piece: a call that moves a large folder
    makes many of them, as many as the platform's ceiling allows."""

    upload: float = 20.0
    start: float = 15.0
    status: float = 5.0
    list_files: float = 10.0
    download: float = 20.0
    logs: float = 5.0
    cancel: float = 10.0
    capacity: float = 10.0

    def __post_init__(self) -> None:
        limit = PLATFORM_CEILING - MARGIN
        for name, seconds in asdict(self).items():
            # bool is an int to Python; as a budget it is a typo, not 1 second.
            is_number = isinstance(seconds, int | float) and not isinstance(seconds, bool)
            if not is_number or not math.isfinite(seconds) or not 0 < seconds <= limit:
                raise ContractError(
                    f"budget.{name}={seconds!r}: must be a number of seconds, more than 0 and "
                    f"at most {limit:g} (the platform stops a call at {PLATFORM_CEILING:g})"
                )
        # Calls one command makes back to back share the platform's ceiling.
        together = (
            (
                "budget.start + budget.status",
                self.start + self.status,
                "a start that runs out of time is followed by status in the same call",
            ),
            (
                "2 x budget.list_files + budget.start + budget.status",
                2 * self.list_files + self.start + self.status,
                "run lists what arrived, re-lists it before starting, starts and checks",
            ),
            (
                "budget.list_files + budget.upload",
                self.list_files + self.upload,
                "run lists what arrived before it sends a piece",
            ),
            (
                "budget.list_files + budget.download",
                self.list_files + self.download,
                "fetch lists what is there before it copies a piece",
            ),
            ("2 x budget.logs", 2 * self.logs, "logs without an offset asks twice"),
            (
                "budget.cancel + budget.status",
                self.cancel + self.status,
                "cancel is followed by status",
            ),
        )
        for name, seconds, why in together:
            if seconds > limit:
                raise ContractError(
                    f"{name} = {seconds:g}: must be at most {limit:g}, because {why}"
                )


@dataclass(frozen=True)
class EnvVar:
    """One environment variable the TOOL reads to reach the backend — a
    cluster token, an endpoint. A job's own variables are files in its folder
    (D3); its secrets are the execution side's (D5). The platform's panel
    shows these; never a gate."""

    name: str
    description: str | None = None
    required: bool | None = None
    """True: counted as missing until set. False: optional. None: listed,
    never nagged about. Leave it None unless you are sure."""
