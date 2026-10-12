"""The interface a backend implements.

Four calls are required; four are optional. An optional call a backend does
not override is a call it does not support, and the shell never offers it to
the model — so the model cannot try something only to be told no.

How long each call may take is not written here: it is the backend's
`Contract.budget`, enforced by the shell. What each call must DO is.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from job_contract.contract import Contract, ContractError, JobStatus, Submitted

#: The calls every backend has.
REQUIRED = ("submit", "find_by_key", "get", "list")
#: The calls a backend may have. Not overriding one means not supporting it.
OPTIONAL = ("update", "cancel", "logs", "fetch")


def _callable(attribute: object) -> bool:
    """Whether a class attribute is a call a backend can make: a function that
    is not abstract. A property, None or an abstract method is not."""
    return callable(attribute) and not getattr(attribute, "__isabstractmethod__", False)


class JobError(Exception):
    """A failure the model should hear about. The exit code tells it what to
    do next; the message is the detail."""

    exit_code = 1


class Retryable(JobError):
    """Calling again may work: the backend was briefly unreachable, or an
    argument the model can fix was wrong. The model may call again; nothing
    retries on its own, because a call may have side effects."""

    exit_code = 2


class NeedsAction(JobError):
    """A person must act first, so calling again changes nothing. Name what is
    missing in full — `set GPU_API_TOKEN`, not "log in" — because that name is
    what lets the platform offer the person a way to fix it."""

    exit_code = 3


class NotFound(JobError):
    """The backend is certain this job does not exist (for this credential).
    Not the same as being unable to say: for that, return `phase="unknown"`."""

    exit_code = 1


class JobBackend(ABC):
    """Talks to one backend. Subclass it, declare `contract`, implement the
    required calls and whichever optional ones the backend supports."""

    contract: ClassVar[Contract]

    @classmethod
    def check(cls) -> None:
        """Refuse a backend that cannot keep its contract. Loading a backend
        runs this — the CLI and the shape tests alike — so an incomplete one
        fails CI instead of shipping a command that fails on every call.

        Not done when a class is defined: a base class sharing helpers between
        backends is incomplete on purpose, and only the class that is loaded
        has to be whole."""
        if not isinstance(getattr(cls, "contract", None), Contract):
            raise ContractError(
                f"{cls.__name__} declares no `contract`; every backend says once what it "
                "accepts, what makes two submits the same job, and how long each call takes"
            )
        # Abstract, None or a property: none of them can be called, so the
        # command built on it would fail on every call.
        missing = [name for name in REQUIRED if not _callable(getattr(cls, name, None))]
        if missing:
            raise ContractError(f"{cls.__name__} does not implement the required {missing}")
        has_update = "update" in cls.offers()
        has_schema = cls.contract.patch_schema is not None
        if has_update and not has_schema:
            raise ContractError(
                f"{cls.__name__} implements `update` but its contract has no patch_schema: "
                "the model would have no way to know what a patch may say"
            )
        if has_schema and not has_update:
            raise ContractError(
                f"{cls.__name__}'s contract declares a patch_schema but it does not implement "
                "`update`: the schema would describe a call that does not exist"
            )

    @classmethod
    def offers(cls) -> tuple[str, ...]:
        """The calls this backend supports: every required one, plus each
        optional one it overrides."""
        # `fetch = None` in a subclass switches off what a parent provided.
        own = tuple(
            name
            for name in OPTIONAL
            if getattr(cls, name, None) is not getattr(JobBackend, name)
            and _callable(getattr(cls, name, None))
        )
        return REQUIRED + own

    # ── required ────────────────────────────────────────────────────────────

    @abstractmethod
    def submit(self, spec: dict[str, Any], key: str, item_id: str | None) -> Submitted:
        """Send the job without waiting for it to run; say which job it is and
        whether this call started it (`Submitted.created`).

        `key` is derived by the shell from `contract.idempotency` and the item.
        Sending the same key again within `contract.idempotency.window_seconds`
        of the first MUST return that job with `created=False` and MUST NOT
        start a second one: an answer can be lost after the job was accepted,
        and whoever sends it again — the model, or a person — cannot tell.
        After the window, the same key starts a new job.

        `item_id` is the item it is sent from (None outside the platform).
        Record it and report it back as `JobStatus.item_id`; it is a label for
        the model, not a permission — who may see or change a job is decided
        by the credential."""

    @abstractmethod
    def find_by_key(self, key: str) -> JobStatus | None:
        """The job submitted under `key` within the window, or None if none was.
        After a submit that ran out of time, this is how the shell learns
        whether it landed."""

    @abstractmethod
    def get(self, job_id: str) -> JobStatus:
        """One job, read-only. Could not ask the backend just now: return
        `phase="unknown"`. Certain it does not exist: raise `NotFound`."""

    @abstractmethod
    def list(self, cursor: str | None, limit: int) -> tuple[list[JobStatus], str | None]:
        """The jobs this credential can see, newest first, read-only — NOT only
        this item's (D42): a person asking about the queue expects the queue.
        One page and the cursor of the next, or None after the last."""

    # ── optional: not overriding one means not supporting it ────────────────

    def update(self, job_id: str, patch: dict[str, Any]) -> JobStatus:
        """Apply `patch` (shaped by `contract.patch_schema`). Returns once the
        backend has ACCEPTED the change, which is not the same as it having
        taken effect; `get` says when it has."""
        raise NotImplementedError

    def cancel(self, job_id: str) -> JobStatus:
        """Stop the job. Cancelling a job that already finished is not an
        error: return its status."""
        raise NotImplementedError

    def logs(self, job_id: str, offset: int, limit: int) -> tuple[str, int]:
        """Up to `limit` characters of the log from `offset`, and the log's
        TRUE total length — not this slice's — so the model knows how much it
        has not seen."""
        raise NotImplementedError

    def fetch(self, job_id: str, name: str, dest: str) -> str:
        """Write output `name` to `dest`, a path relative to the working
        directory (the user's workspace), and return the path written."""
        raise NotImplementedError
