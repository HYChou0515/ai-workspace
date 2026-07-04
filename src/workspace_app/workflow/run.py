"""``WorkflowRun`` — the persisted record of one workflow run (#100, manual §13).

The **filesystem is the journal** (manual §9): step results live as workspace
artifacts, not on this resource. So ``WorkflowRun`` records *status*, not step
outputs — enough to answer "where is the run / which phase broke" live (via SSE)
and after the fact (by querying this resource), and to list an item's runs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from msgspec import Struct, field

from ..apps.base import IndexedFields


class RunStatus(StrEnum):
    """A run's lifecycle (manual §13). ``pending`` before the driver starts;
    ``awaiting_human`` while suspended at a ``human_gate``; the rest terminal."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class PhaseState(Struct):
    """Per-phase progress for the phase-level diagram (manual §12). ``status`` is
    one of pending/running/passed/failed/skipped/awaiting_human; the counters
    drive a phase's "12/20 · 1 failed" sub-progress when it loops over a batch."""

    phase: str
    status: str = "pending"
    done: int = 0
    total: int = 0
    failed: int = 0


class StepState(Struct):
    """Per-step status for the step board (#178) — so a long step doesn't look dead.
    Bounded by collapse: loop elements (``key != ""``) live here only while running,
    then fold into the phase ``done`` counter on terminal; distinct-named steps
    (``key == ""``) persist with their final ``status`` + duration. ``started`` /
    ``ended`` are server epoch ms (reload-safe elapsed); ``attempts`` counts retries.
    Stdout stays ephemeral (streamed as ``StepOutput``), not stored here."""

    phase: str
    name: str
    key: str = ""
    status: str = "running"
    """running | retrying | passed | skipped | failed."""
    attempts: int = 1
    reason: str = ""
    started: int | None = None
    ended: int | None = None


class Failure(Struct):
    """A collected per-element failure (loop skip+collect policy, manual §11)."""

    key: str
    error: str
    phase: str = ""


class PendingDecision(Struct):
    """The open human gate while a run is ``awaiting_human`` (manual §10, §13) — what
    the FE renders as the review card. ``decided_by`` records who answered."""

    phase: str
    title: str
    summary: str = ""
    allow: list[str] = field(default_factory=list)
    decided_by: str = ""


class SteerInputEdit(Struct):
    """One input-file rewrite in a steer plan (#288, manual §10): the full new
    ``content`` for ``path`` (a workspace path *outside* the journal). Full-content
    writes — not diffs — dodge the tool-arg unreliability that bites long content (#107)."""

    path: str
    content: str


class SteerPlan(Struct):
    """A proposed steer (#288, manual §10), produced by the read-only steerer turn and
    reviewed before it applies. Two generic moves: rewrite ``input_edits`` + ``invalidate``
    steps (delete their artifacts → force re-run; downstream cascades via input-hash, §9).
    ``instruction`` is the human's free-text ask; ``rationale`` is the steerer's summary;
    ``decided_by`` records who confirmed (audit, §15). Stored as ``WorkflowRun.pending_steer``
    while awaiting confirm."""

    instruction: str = ""
    rationale: str = ""
    input_edits: list[SteerInputEdit] = field(default_factory=list)
    invalidate: list[str] = field(default_factory=list)
    decided_by: str = ""


class WorkflowRun(Struct):
    item_id: str
    """Indexed — the owning item (any App's WorkItem ``resource_id``; #89). One
    item may host multiple sequential runs (manual §14)."""

    captured_user: str
    """The acting user resolved at trigger time (manual §15). Background steps and
    resume run under ``rm.using(user=captured_user)`` since they have no request
    context, so ``created_by`` / ingestion attribution / notifications stay right."""

    chat_id: str = ""
    """The workflow CHAT this run drives (topic-hub P8, manual §3) — the opaque
    stream/turn key the orchestrator publishes + enqueues on. "" → the legacy path
    (key falls back to ``item_id``: the item's default chat / broadcast stream)."""

    workflow_id: str = ""
    """Which of the profile's workflows this run executes (manual §4). "" → the
    profile's legacy singular / sole workflow. Durable so resume (decide) reloads
    the right ``run.py`` after a restart."""

    status: RunStatus = RunStatus.PENDING
    current_phase: str = ""
    phases: list[PhaseState] = field(default_factory=list)
    steps: list[StepState] = field(default_factory=list)
    """Per-step board (#178) — bounded by collapse (loop elements drop on terminal;
    distinct-named steps keep their final status + duration). Additive: runs written
    before #178 just have an empty list, so no migration is needed."""
    failures: list[Failure] = field(default_factory=list)
    started: int | None = None
    """Epoch ms when the driver started the run; None while ``pending``."""
    ended: int | None = None
    """Epoch ms when the run reached a terminal status; None until then."""
    progress_at: int | None = None
    """Epoch ms of the last progress patch — a liveness heartbeat (#429 P8). Advances on
    every phase/step transition (`_apply_progress`), so a run left RUNNING by a dead pod goes
    stale while a live one stays fresh; the orphan sweeper flags `now - progress_at > grace`.
    Additive (None on runs written before #429), so no migration is needed."""
    result: dict[str, Any] | None = None
    """The ``run()`` return value (a summary), persisted on terminal."""
    pending_decision: PendingDecision | None = None
    """Set while ``awaiting_human`` — the open gate the FE renders (manual §10)."""
    pending_steer: SteerPlan | None = None
    """Set while ``awaiting_human`` for a steer plan awaiting confirm (#288). Mutually
    exclusive with ``pending_decision`` — the FE picks the steer card vs. the gate card by
    which is set, and ``decide()`` guards on ``pending_decision`` so they never collide."""


# What the platform registers: the resource + its indexes (manual §13). item_id
# so "an item's runs" is a query; status so "active runs" (concurrency cap, §16)
# is a query — never a full scan (see reference_specstar_indexed_queries).
MODEL = WorkflowRun
INDEXED_FIELDS: IndexedFields = ["item_id", "status"]
