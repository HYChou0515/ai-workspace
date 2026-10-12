"""The job tool contract: what a backend implements, and what it declares once.

A *job* is work that runs somewhere else and is asked about later. The tool
shell (the template) calls a backend through `JobBackend`, enforces each call's
time budget, and decides what the model is told from the result. Everything a
backend must declare — what a job spec looks like, what makes two submits "the
same", how long each call may take — is declared once, on its `Contract`, and
read from there by the shell, the shape tests and the generated description.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, get_args

#: The platform stops a tool call after this many seconds (docs/tool-authoring.md §6).
PLATFORM_CEILING = 60.0
#: Kept back from every budget so the shell can still tell the model what happened.
MARGIN = 5.0


class ContractError(Exception):
    """A declaration that cannot be honoured. Raised as early as it can be
    known: a contract value when it is written, a backend's missing or
    uncallable call when the backend is loaded (`JobBackend.check`), and a
    `JobStatus` with a phase outside the three when the backend returns it."""


Phase = Literal["running", "done", "unknown"]
PHASES: tuple[Phase, ...] = get_args(Phase)


@dataclass(frozen=True)
class JobStatus:
    """One job as the backend sees it right now."""

    job_id: str
    phase: Phase
    """The only field the shell reads. `running`: not finished — paused,
    queued and preempted all count. `done`: finished, however it ended.
    `unknown`: the backend could not say just now — never "does not exist";
    for that, raise `NotFound`."""
    state: str
    """The backend's own word for where the job is ("suspended", "failed:
    OOM"). Shown to the model verbatim; the shell never interprets it."""
    detail: dict[str, Any] = field(default_factory=dict)
    """Anything else worth showing. Printed verbatim."""
    item_id: str | None = None
    """The item the job was submitted from, as the backend recorded it at
    `submit` — None when unknown (submitted elsewhere, or with no item). The
    shell uses it only to tell the model which jobs came from this item; what
    a job may be seen or changed by is the credential's business (D42, D43)."""

    def __post_init__(self) -> None:
        if self.phase not in PHASES:
            raise ContractError(
                f"phase={self.phase!r}: must be one of {list(PHASES)}; "
                "the backend's own word for it belongs in `state`"
            )


@dataclass(frozen=True)
class Budget:
    """How long each call may take, in seconds. The shell stops a call that
    runs past its budget and tells the model what that means for that call."""

    submit: float = 10.0
    find_by_key: float = 5.0
    get: float = 3.0
    list: float = 5.0
    update: float = 10.0
    cancel: float = 10.0
    logs: float = 5.0
    fetch: float = 50.0

    def __post_init__(self) -> None:
        limit = PLATFORM_CEILING - MARGIN
        for name, seconds in asdict(self).items():
            # bool is an int to Python; as a budget it is a typo, not 1 second.
            is_number = isinstance(seconds, int | float) and not isinstance(seconds, bool)
            if not is_number or not 0 < seconds <= limit:
                raise ContractError(
                    f"budget.{name}={seconds!r}: must be a number of seconds, more than 0 and "
                    f"at most {limit:g} (the platform stops a call at {PLATFORM_CEILING:g})"
                )
        # A submit that runs out of time is followed, in the same call, by
        # find_by_key — so the two share one ceiling.
        if self.submit + self.find_by_key > limit:
            raise ContractError(
                f"budget.submit + budget.find_by_key = {self.submit + self.find_by_key:g}: "
                f"must be at most {limit:g}, because a submit that runs out of time is "
                "followed by find_by_key in the same call"
            )


@dataclass(frozen=True)
class Submitted:
    """What `submit` answers (D51): the job, and whether this call started it.
    Only the backend knows the second part, and the shell must tell the model
    which — announcing a day-old, finished job as "submitted" would be false."""

    job_id: str
    created: bool

    def __post_init__(self) -> None:
        # None would read as the shell's own "cannot say"; a truthy string as
        # "a new job". Only True or False says which.
        if not isinstance(self.created, bool):
            raise ContractError(f"Submitted.created={self.created!r}: must be True or False")


@dataclass(frozen=True)
class Idempotency:
    """What makes two submits the same job.

    The shell derives the key from this rule and hands it to `submit`; a
    backend never derives one itself, so there is exactly one answer to "is
    this the job I already sent?"."""

    fields: tuple[str, ...]
    """The spec fields that decide sameness. A field left out (a free-text
    note, say) never makes a resubmit look like a new job."""

    window_seconds: float = 86400
    """How long the same work stays the same job (D50). Within it, sending the
    same key returns the job already there; after it, the same work starts a
    new one. A day stops every retry after a lost answer — those come within
    minutes — and still lets the same work be run again tomorrow."""

    def __post_init__(self) -> None:
        seconds = self.window_seconds
        is_number = isinstance(seconds, int | float) and not isinstance(seconds, bool)
        # NaN compares false with everything, so as a window it would silently
        # switch sameness off; infinity would make "tomorrow" never come.
        if not is_number or not math.isfinite(seconds) or seconds <= 0:
            raise ContractError(
                f"window_seconds={seconds!r}: must be a positive, finite number of seconds"
            )

    def identity(self, spec: dict[str, Any]) -> Any:
        """What two submits must share to be the same job. By default the
        declared `fields`, as sent.

        Override it to compare loosely — ignore case, strip spaces, reach into a
        nested field (D49). Return only what is compared: the item scope and the
        hash are added by `key`, which is not to be overridden. It must give
        the same, JSON-serialisable answer every time for the same spec — no
        clock, no randomness — or every resubmit becomes a new job; the shape
        tests check it on `Contract.example_spec`. Keep `fields` naming what it
        reads: the model is told those names."""
        return {name: spec.get(name) for name in self.fields}

    def key(self, spec: dict[str, Any], *, item_id: str | None) -> str:
        """The key for `spec` sent from `item_id`. Sameness is per item (D41): a
        job's outputs land in one item's workspace, so the same work from two
        items is two jobs. With no item (the MCP runner, D47) the identity alone
        decides — and None never equals any item's id, empty included."""
        canonical = json.dumps(
            [item_id, self.identity(spec)], sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def overrides_identity(self) -> bool:
        return type(self).identity is not Idempotency.identity


@dataclass(frozen=True)
class EnvVar:
    """One environment variable the backend reads. A hint for the person
    setting it up — the platform's panel shows it — never a gate."""

    name: str
    description: str | None = None
    required: bool | None = None
    """True: counted as missing until set. False: optional. None: listed,
    never nagged about. Leave it None unless you are sure."""


@dataclass(frozen=True)
class Contract:
    """Everything a backend declares about itself, declared once.

    The shell validates against these schemas, enforces this budget and
    derives keys by this rule; the shape tests and the generated description
    read the same object. None of it is restated anywhere else."""

    spec_schema: dict[str, Any]
    """JSON schema of what `submit` accepts. Its fields are the author's."""

    idempotency: Idempotency

    budget: Budget = Budget()

    patch_schema: dict[str, Any] | None = None
    """JSON schema of what `update` accepts, or None when there is no `update`.
    What a patch can say — a priority, a pause — is the author's."""

    domain_description: str = ""
    """What these jobs ARE, in the author's words ("Trains a model on the
    shared GPU cluster."). The model reads it first, before the template's
    sentences about how a job behaves — which come from this contract and are
    never written by hand."""

    env: tuple[EnvVar, ...] | None = None
    """The environment variables the backend reads. None says nothing — which
    is NOT the same as `()`, "needs none": the panel tells the two apart."""

    example_spec: dict[str, Any] | None = None
    """One valid spec. Required when `idempotency.identity` is overridden: the
    shape tests run it there, twice, and demand the same answer."""

    def __post_init__(self) -> None:
        if self.idempotency.overrides_identity() and self.example_spec is None:
            raise ContractError(
                "idempotency overrides identity(), so the contract needs an example_spec: "
                "the shape tests check on it that the same spec always gives the same answer"
            )
        names = [var.name for var in self.env or ()]
        twice = sorted({name for name in names if names.count(name) > 1})
        if twice:
            raise ContractError(f"env declares {twice} more than once")
        if not self.idempotency.fields:
            raise ContractError(
                "idempotency.fields must name at least one spec field: with none, the key "
                "depends on the item alone and every submit from it is the same job"
            )
        known = set(self.spec_schema.get("properties", {}))
        unknown = [name for name in self.idempotency.fields if name not in known]
        if unknown:
            raise ContractError(
                f"idempotency.fields names {unknown}, which spec_schema does not have "
                f"(it has {sorted(known)})"
            )
