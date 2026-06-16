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


class WorkflowRun(Struct):
    item_id: str
    """Indexed — the owning item (any App's WorkItem ``resource_id``; #89). One
    item may host multiple sequential runs (manual §14)."""

    captured_user: str
    """The acting user resolved at trigger time (manual §15). Background steps and
    resume run under ``rm.using(user=captured_user)`` since they have no request
    context, so ``created_by`` / ingestion attribution / notifications stay right."""

    status: RunStatus = RunStatus.PENDING
    current_phase: str = ""
    phases: list[PhaseState] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    started: int | None = None
    """Epoch ms when the driver started the run; None while ``pending``."""
    ended: int | None = None
    """Epoch ms when the run reached a terminal status; None until then."""
    result: dict[str, Any] | None = None
    """The ``run()`` return value (a summary), persisted on terminal."""
    pending_decision: PendingDecision | None = None
    """Set while ``awaiting_human`` — the open gate the FE renders (manual §10)."""


# What the platform registers: the resource + its indexes (manual §13). item_id
# so "an item's runs" is a query; status so "active runs" (concurrency cap, §16)
# is a query — never a full scan (see reference_specstar_indexed_queries).
MODEL = WorkflowRun
INDEXED_FIELDS: IndexedFields = ["item_id", "status"]
