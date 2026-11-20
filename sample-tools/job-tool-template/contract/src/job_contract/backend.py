"""The interface a backend implements.

Seven calls are required and one is optional. The backend moves bytes and
answers; the tool shell decides everything else — which files make up a job,
its id, how a large folder is split across calls, when the job is started,
what the model is told (docs/plan-job-queue.md).

How long each call may take is the backend's `budget`, enforced by the shell.
What each call must DO is written here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from job_contract.contract import Budget, Capacity, ContractError, Entry, EnvVar, Status

#: The calls every backend has.
REQUIRED = ("upload", "start", "status", "list_files", "download", "logs", "cancel")
#: The calls a backend may have. Not overriding one means not supporting it.
OPTIONAL = ("capacity",)
#: The commands the model is offered. `queue` exists only with `capacity`.
COMMANDS = ("run", "jobs", "queue", "files", "fetch", "logs", "cancel")


def _callable(attribute: object) -> bool:
    """Whether a class attribute is a call a backend can make: a function that
    is not abstract. A property, None or an abstract method is not."""
    return callable(attribute) and not getattr(attribute, "__isabstractmethod__", False)


class JobError(Exception):
    """A failure the model should hear about. The exit code tells it what to
    do next; the message is the detail."""

    exit_code = 1


class Retryable(JobError):
    """Calling again may work: the backend was briefly unreachable, a job is
    waiting on something that will pass, or something the model can fix was
    wrong (say which). The model may call again; nothing retries on its own."""

    exit_code = 2


class NeedsAction(JobError):
    """A person must act first, so calling again changes nothing. Name what is
    missing in full — `set CLUSTER_TOKEN`, not "log in" — because that name is
    what lets the platform offer the person a way to fix it."""

    exit_code = 3


class NotFound(JobError):
    """The backend is certain this job (or file) does not exist for this
    credential. Not the same as being unable to say: for a job, return
    `Status("unknown")`."""

    exit_code = 1


class JobBackend(ABC):
    """Talks to one execution side. Subclass it, write `run_description` and
    `resources_schema`, and implement the required calls."""

    run_description: ClassVar[str]
    """How to prepare a folder for this backend, in your words — the model
    writes the folder from this alone (D4). Say which file is run and how
    (`bash run.sh`), which files set up the environment (`requirements.txt`
    installed first, if present; otherwise the default image), where variables
    go (`.env`), what the machine has (Python, CUDA, datasets at `/data`, no
    network) and which secrets are already set there. Outputs are whatever the
    job leaves in its folder. The tool adds its own sentences after yours."""

    resources_schema: ClassVar[dict[str, Any]]
    """JSON schema of what a job may ask for — GPUs, memory, hours, a
    priority. The model fills it; the shell checks it before calling you."""

    descriptions: ClassVar[dict[str, str]] = {}
    """Your own description for any command but `run` (whose description is
    `run_description`). Leave one out to keep the template's."""

    env: ClassVar[tuple[EnvVar, ...] | None] = None
    """The variables THIS TOOL reads to reach you (a token, an endpoint).
    None says nothing — which is not `()`, "needs none"."""

    budget: ClassVar[Budget] = Budget()

    @classmethod
    def check(cls) -> None:
        """Refuse a backend that cannot keep the contract. Loading a backend
        runs this — the CLI and the shape tests alike — so a bad one fails CI
        instead of shipping a command that fails on every call.

        Not done when a class is defined: a base class sharing helpers between
        backends is incomplete on purpose; only the loaded class must be whole."""
        missing = [name for name in REQUIRED if not _callable(getattr(cls, name, None))]
        if missing:
            raise ContractError(f"{cls.__name__} does not implement the required {missing}")

        text = getattr(cls, "run_description", None)
        if not isinstance(text, str) or not text.strip():
            raise ContractError(
                f"{cls.__name__} has no run_description: the model learns from it alone how "
                "to lay out a folder for you — which file runs, what sets up the environment, "
                "what the machine has"
            )

        schema = getattr(cls, "resources_schema", None)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ContractError(
                f"{cls.__name__}.resources_schema must be a JSON schema with "
                f'"type": "object": a job\'s resources are passed as one object'
            )

        cls._check_descriptions()

        names = [var.name for var in cls.env or ()]
        twice = sorted({name for name in names if names.count(name) > 1})
        if twice:
            raise ContractError(f"{cls.__name__}.env declares {twice} more than once")

        if not isinstance(cls.budget, Budget):
            raise ContractError(f"{cls.__name__}.budget must be a Budget, got {cls.budget!r}")

    @classmethod
    def _check_descriptions(cls) -> None:
        offered = cls.commands()
        for name, text in cls.descriptions.items():
            if name == "run":
                raise ContractError(
                    f"{cls.__name__}.descriptions names run: its description is run_description"
                )
            if name not in offered:
                raise ContractError(
                    f"{cls.__name__}.descriptions names {name!r}, which is not one of its "
                    f"commands {list(offered)}"
                    + (" (queue exists only with capacity)" if name == "queue" else "")
                )
            if not isinstance(text, str) or not text.strip():
                raise ContractError(f"{cls.__name__}.descriptions[{name!r}] is empty")

    @classmethod
    def offers(cls) -> tuple[str, ...]:
        """The calls this backend supports: every required one, plus each
        optional one it overrides."""
        # `capacity = None` in a subclass switches off what a parent provided.
        own = tuple(
            name
            for name in OPTIONAL
            if getattr(cls, name, None) is not getattr(JobBackend, name)
            and _callable(getattr(cls, name, None))
        )
        return REQUIRED + own

    @classmethod
    def commands(cls) -> tuple[str, ...]:
        """The commands the model is offered for this backend."""
        has_queue = "capacity" in cls.offers()
        return tuple(name for name in COMMANDS if name != "queue" or has_queue)

    # ── required ────────────────────────────────────────────────────────────

    @abstractmethod
    def upload(self, job_id: str, path: str, offset: int, data: bytes) -> None:
        """Write `data` into file `path` of job `job_id`'s folder, starting at
        byte `offset`; create the folder and the file on first use.

        The shell sends a folder in pieces, over as many calls as it takes,
        and reads what has arrived from `list_files`. So: write at `offset`
        exactly (never append blindly), and never truncate — the same piece
        sent twice must leave the same file. `path` is relative, uses `/`, and
        has no `..`; the shell has checked it."""

    @abstractmethod
    def start(self, job_id: str, resources: dict[str, Any]) -> None:
        """Run the job whose folder has arrived, asking for `resources` (already
        checked against `resources_schema`). Return once it is accepted — queued
        is enough. How it runs is yours, as `run_description` says.

        Called only after every file has arrived. Starting a `job_id` that is
        already started MUST do nothing: the answer to a start can be lost, and
        the shell may call again. Refused for quota: `Retryable` when waiting
        will free it, `NeedsAction` when a person must act."""

    @abstractmethod
    def status(self, job_id: str) -> Status:
        """Where the job is. Raise `NotFound` when it was never started (its
        files may have arrived); return `Status("unknown")` when you cannot ask
        just now."""

    @abstractmethod
    def list_files(self, job_id: str, path: str) -> list[Entry]:
        """Everything under `path` in the job's folder (`"."` for all of it),
        at any depth, files and folders, with their real sizes. Works before
        `start` too: it is how the shell learns how much of an upload arrived.
        `NotFound` when the job's folder does not exist yet."""

    @abstractmethod
    def download(self, job_id: str, path: str, offset: int, limit: int) -> bytes:
        """Up to `limit` bytes of file `path` in the job's folder, from byte
        `offset`. Fewer (or none) at the end of the file."""

    @abstractmethod
    def logs(self, job_id: str, offset: int, limit: int) -> tuple[str, int]:
        """Up to `limit` characters of the job's log from `offset`, and the
        log's TRUE total length — not this slice's — so the model knows how
        much it has not seen. `("", 0)` before there is any."""

    @abstractmethod
    def cancel(self, job_id: str) -> None:
        """Stop the job. Cancelling a job that already finished is not an error."""

    # ── optional: not overriding it means not supporting it ─────────────────

    def capacity(self) -> Capacity:
        """The machines, the queue and the credential's quota. Without it the
        model has no `queue` command."""
        raise NotImplementedError
