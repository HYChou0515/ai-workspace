"""``WorkflowRun`` — the persisted record of one workflow run (#100, manual §13).

The **filesystem is the journal** (manual §9): step results live as workspace
artifacts, not on this resource. So ``WorkflowRun`` records *status*, not step
outputs — enough to answer "where is the run / which phase broke" live (via SSE)
and after the fact (by querying this resource), and to list an item's runs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import msgspec
from msgspec import Struct, field

from ..apps.base import IndexedFields

#: How a WorkflowRun's id begins (specstar prefixes an id with its model's
#: name). An execution's tab path in the web UI IS its run id, and `show_file`
#: takes it as the path (plan-workflow-out-of-chat D7 / D8). Mirrors `PREFIX` in
#: `web/src/lib/executionTab.ts`; both are pinned by tests.
EXECUTION_PATH_PREFIX = "workflow-run:"
#: The `mime` an execution's card is declared with (the shown-files declaration
#: needs one); the chat picks the execution card by its path, not by this.
EXECUTION_MIME = "application/x-workflow-execution"


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
    one of pending/running/passed/failed/skipped/stopped/awaiting_human (at most
    one running: `settle_phases`); the counters
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


class PageJob(Struct):
    """What a page started with `startTool` / `startAgent` / `callAgent`
    (`docs/plan-wui-call-agent.md` D5) — set on the run INSTEAD of a workflow.

    ``kind`` is ``tool`` or ``agent``; ``name`` the tool or sub-agent; ``input``
    what the page sent (the agent's text, or the tool's args as JSON), kept so
    the run record says what was asked. ``deadline`` (epoch ms) is the job's
    total-time cap: the work lives in the memory of the pod that started it and
    nothing re-drives it after a restart, so a row still active past it is over
    whatever it says (`WorkflowOrchestrator.page_job_state`)."""

    kind: str
    name: str
    input: str = ""
    deadline: int = 0


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
    trigger_payload: dict[str, Any] = field(default_factory=dict)
    """What the declaration that started this run asked for (#WUI P15).

    Opaque to the platform: a page's `schedules.json` row carries a `with`
    object, and it arrives here untouched. Everything domain-shaped — which
    line, which recipients, which report — lives in here, which is what keeps
    the platform from ever learning what a report is.

    Empty for a human launch and for an engineer-authored trigger. Additive, so
    runs written before this just have `{}` and no migration is needed.
    """

    origin_trigger: str = ""
    """The event trigger that spawned this run (#429 P9), or "" for a human / schedule launch.
    A run's entity writes carry this so the dispatcher never re-fires the run's OWN trigger
    (recursion guard 1)."""
    trigger_depth: int = 0
    """This run's depth in the event-trigger chain (#429 P9). A first-level (human / schedule)
    run is 0; each event-triggered hop adds one. Its entity writes carry it so an indirect
    cycle hits the global depth cap (recursion guard 2)."""
    by_hand: bool = False
    """Started by a schedule's "run now", not by its time — what the schedules
    overview tags "手動" (`docs/plan-schedule-overview-polish.md`). Additive: a
    run written before this reads False, as a scheduled one."""
    from_schedule: bool = False
    """Started by a schedule — by its time or by its "run now" — not by a person
    launching the workflow (docs/plan-tool-usage-stats.md §3.4: its tool calls
    are counted on the schedule surface). Additive: a run written before this
    reads False."""
    page_job: PageJob | None = None
    """Set when a PAGE started this run as a job rather than a workflow (#942).
    Additive: every run written before it reads None, a workflow run."""
    result: dict[str, Any] | None = None
    """The ``run()`` return value (a summary), persisted on terminal."""
    pending_decision: PendingDecision | None = None
    """Set while ``awaiting_human`` — the open gate the FE renders (manual §10)."""
    pending_steer: SteerPlan | None = None
    """Set while ``awaiting_human`` for a steer plan awaiting confirm (#288). Mutually
    exclusive with ``pending_decision`` — the FE picks the steer card vs. the gate card by
    which is set, and ``decide()`` guards on ``pending_decision`` so they never collide."""


#: What the phase a run ended in may read before its last status settles it.
_ENDED_IN = ("running", "stopped")


def settle_phases(run: WorkflowRun) -> list[PhaseState]:
    """``run.phases`` with at most one ``running``: the phase the run is in, and
    only while it is live (plan-one-running-phase D1 / D2). The one place a
    phase's status follows from the run's progress — the orchestrator writes it
    and the run routes serve it, so a row stored before this reads right too.

    A phase is set ``running`` on entry and nothing else leaves it; every phase
    walked so far used to read running at once, so the execution tab streamed
    the next step under the first one's row and kept that row locked open — for
    good, once an error or a Stop froze it. Here a running phase the run is no
    longer in has passed; the one it ended in failed (ERROR) or stopped
    (CANCELLED). At a gate the run is in the gate's phase — a gate enters it
    (`StepStarted`) before it pauses — so that one is left as it is (#176)."""
    status = run.status
    live = status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.AWAITING_HUMAN)
    anchor = run.current_phase if live else None  # ended: none is still being worked on
    ended_as = {RunStatus.ERROR: "failed", RunStatus.CANCELLED: "stopped"}.get(status)

    def settled(p: PhaseState) -> PhaseState:
        if p.phase == anchor:
            return p
        # The phase it ended in follows its LAST status: a wall-clock cap first
        # cancels the driver (stopped) and then records the error (failed). Only
        # this function writes `stopped`, so re-reading it is safe.
        if ended_as is not None and p.phase == run.current_phase and p.status in _ENDED_IN:
            return msgspec.structs.replace(p, status=ended_as)
        if p.status == "running":
            return msgspec.structs.replace(p, status="passed")
        return p

    return [settled(p) for p in run.phases]


def with_settled_phases(run: Any, **changes: Any) -> WorkflowRun:
    """``run`` with ``changes`` applied and its phases settled (`settle_phases`):
    what every write of a run stores, so no write — a step entering a phase, a
    gate, an error, a Stop from any of the places that record one — can leave a
    phase running that the run is no longer in."""
    assert isinstance(run, WorkflowRun)
    updated = msgspec.structs.replace(run, **changes)
    return msgspec.structs.replace(updated, phases=settle_phases(updated))


def is_execution(run: WorkflowRun) -> bool:
    """Whether ``run`` executes a workflow — what the Workflows sidebar lists and
    what competes for "newest" of its workflow. A page job (#942) never does: it
    has no workflow, and its ``workflow_id`` is "", the id a profile's first
    workflow runs under too, so comparing ids alone made a page's job supersede
    that workflow's execution (and, while it ran, read as one executing)."""
    return run.page_job is None


def is_execution_of(run: WorkflowRun, workflow_id: str) -> bool:
    """Whether ``run`` is an execution of ``workflow_id`` — one of the runs that
    share its journal (`is_execution`)."""
    return is_execution(run) and run.workflow_id == workflow_id


# What the platform registers: the resource + its indexes (manual §13). item_id
# so "an item's runs" is a query; status so "active runs" (concurrency cap, §16)
# is a query — never a full scan (see reference_specstar_indexed_queries).
MODEL = WorkflowRun
INDEXED_FIELDS: IndexedFields = ["item_id", "status"]
