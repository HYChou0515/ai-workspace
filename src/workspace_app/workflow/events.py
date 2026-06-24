"""Workflow run events (#100, manual §12) — phase-level observability.

These ride the SAME per-item broadcast stream as the agent events (a run is a
turn on the item, §5.1), so the FE overlays them on ``MANIFEST.phases`` to draw
"where are we / what broke". They live in the ``workflow`` package (not ``api``)
so the step engine can emit them without importing the API layer; ``api/events``
folds them into its ``AgentEvent`` union + ``to_sse`` (a frozen dataclass with a
``type`` field is all ``to_sse``/``asdict`` needs). Mirrored in ``web/src/events.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PhaseEntered:
    """A new workflow phase began (the first step carrying this ``phase`` ran)."""

    phase: str
    type: Literal["phase_entered"] = "phase_entered"


@dataclass(frozen=True)
class StepStarted:
    """A step began executing (not a cache skip). ``key`` is the loop element."""

    phase: str
    name: str
    key: str = ""
    type: Literal["step_started"] = "step_started"


@dataclass(frozen=True)
class StepOutput:
    """A chunk of a still-running deterministic step's stdout, streamed live (#178)
    so a long sandbox command shows movement instead of looking dead. ``key`` is the
    loop element. Ephemeral: the FE folds it into the running step row, and it is NOT
    persisted on ``WorkflowRun`` (the journal holds the final stdout, manual §9), so
    the orchestrator publishes it on the stream without patching the resource."""

    phase: str
    name: str
    text: str
    key: str = ""
    type: Literal["step_output"] = "step_output"


@dataclass(frozen=True)
class StepPassed:
    """A step's gate passed; its artifact is journaled (manual §9)."""

    phase: str
    name: str
    key: str = ""
    type: Literal["step_passed"] = "step_passed"


@dataclass(frozen=True)
class StepFailed:
    """A step aborted — its gate failed after all retries (``reason`` = why)."""

    phase: str
    name: str
    reason: str = ""
    key: str = ""
    type: Literal["step_failed"] = "step_failed"


@dataclass(frozen=True)
class StepSkipped:
    """A step was skipped — its artifact exists with a matching input-hash (§9)."""

    phase: str
    name: str
    key: str = ""
    type: Literal["step_skipped"] = "step_skipped"


@dataclass(frozen=True)
class StepRetrying:
    """A step's gate failed but retries remain — ``reason`` is fed back (manual §6)."""

    phase: str
    name: str
    reason: str = ""
    key: str = ""
    type: Literal["step_retrying"] = "step_retrying"


@dataclass(frozen=True)
class AwaitingHumanEvent:
    """The run suspended at a ``human_gate`` (manual §10) — the FE renders the
    decision card. Terminal for THIS run task (resumed via the decisions endpoint).
    Named ``…Event`` to avoid colliding with the ``gate.AwaitingHuman`` exception;
    serialized as ``type: "awaiting_human"``."""

    phase: str
    title: str
    type: Literal["awaiting_human"] = "awaiting_human"


WorkflowEvent = (
    PhaseEntered
    | StepStarted
    | StepOutput
    | StepPassed
    | StepFailed
    | StepSkipped
    | StepRetrying
    | AwaitingHumanEvent
)
