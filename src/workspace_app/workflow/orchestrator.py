"""``WorkflowOrchestrator`` — schedules + supervises workflow runs (#100, §13–§17).

The status-only ``driver.run_workflow`` owns one run's terminal status; this owns
everything *around* a run, and is the single object the API routes call:

- builds the run's ``WorkflowHandle`` (wiring drive_turn / run_sandbox / ingest / emit),
- **one active run per item** (§14) + a **global concurrency cap** — excess runs sit
  ``pending`` until a slot frees (§16),
- maps step events → ``WorkflowRun`` per-phase progress AND broadcasts them on the
  item's stream (§12),
- **per-run wall-clock timeout** + **max-steps budget** → ``error`` (§17),
- **releases the sandbox** on terminal / ``awaiting_human`` (§16) + notifies on failure,
- **Stop** (cancel, §10) and the **human-gate decide → resume** cycle (§10).

It is deliberately App-agnostic: the API layer injects ``load_run`` / ``load_manifest``
(profile discovery), ``wire_handle`` (attach the turn/sandbox/ingest drivers),
``publish`` (broadcast on the item's stream), ``release`` (tear the sandbox down) and
``notify_failure``. Tests inject fakes and drive it directly.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import msgspec
from specstar import QB, SpecStar

from ..filestore.protocol import FileStore
from .credential import CredentialBroker
from .driver import _now_ms, run_workflow
from .events import (
    AwaitingHumanEvent,
    PhaseEntered,
    StepFailed,
    StepPassed,
    StepSkipped,
    StepStarted,
)
from .gate import record_decision
from .handle import WorkflowHandle
from .manifest import WorkflowManifest
from .run import PhaseState, RunStatus, WorkflowRun

# A run is "active" (blocks a second start, manual §14) while pending / running /
# paused at a gate. The rest are terminal.
_ACTIVE = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.AWAITING_HUMAN)

ProfileRun = Callable[[WorkflowHandle, Any], Awaitable[Any]]
# Attach the run's leaf drivers (drive_turn / run_sandbox / ingest) to a freshly
# built handle: (wf, run_id, item_id, captured_user).
WireHandle = Callable[[WorkflowHandle, str, str, str], None]


class ActiveRunExists(Exception):
    """A second run was started on an item that already has an active one (§14)."""

    def __init__(self, item_id: str, run_id: str) -> None:
        self.item_id = item_id
        self.run_id = run_id
        super().__init__(f"item {item_id!r} already has an active run ({run_id})")


class NotAwaitingDecision(Exception):
    """A decision was posted to a run that is not paused at a gate (§10)."""


class StepBudgetExceeded(Exception):
    """The run blew its max-steps ceiling — guards runaway loops (manual §17)."""


def _noop_publish(_item_id: str, _event: Any) -> None:
    pass


@dataclass
class WorkflowOrchestrator:
    spec: SpecStar
    store: FileStore
    load_run: Callable[[str, str], ProfileRun]
    load_manifest: Callable[[str, str], WorkflowManifest | None]
    wire_handle: WireHandle
    # (item_id, event) — broadcast a phase/step event on the item's stream. Typed
    # ``Any`` event so the API can pass ``turn_engine.publish`` (which accepts the
    # narrower AgentEvent union) without a contravariance complaint.
    publish: Callable[[str, Any], None] = _noop_publish
    # Release the item's resources (manual §16): ``release(item_id, terminal)``.
    # ``terminal`` is True on done/error/cancelled (tear down sandbox + turn
    # session), False on an ``awaiting_human`` pause (free the sandbox but keep the
    # stream alive for the decision card). Injected by the API layer; None ⇒ no-op.
    release: Callable[[str, bool], Awaitable[None]] | None = None
    # In-app failure notification (manual §17). Injected by the API layer.
    notify_failure: Callable[[WorkflowRun], None] | None = None
    # Run-scoped credentials (manual §15): minted per run, injected into the
    # handle's ``credential`` for sandbox capability calls, revoked on terminal.
    # None ⇒ no credential (the in-process capability path uses the captured user).
    credentials: CredentialBroker | None = None
    credential_ttl_s: float = 3600.0
    max_steps: int = 1000
    run_timeout_s: float | None = None
    step_timeout_s: float | None = None
    concurrency: int = 8
    # Keep at most this many runs per item — older *terminal* runs are pruned when a
    # new run starts (manual §16 retention). 0 ⇒ keep all.
    keep_last_runs: int = 0
    now: Callable[[], int] = _now_ms
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _step_counts: dict[str, int] = field(default_factory=dict)
    _sem: asyncio.Semaphore | None = None

    # ── scheduling ────────────────────────────────────────────────────
    def _semaphore(self) -> asyncio.Semaphore:
        # Lazily built so the cap binds to the running loop (not import time).
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.concurrency)
        return self._sem

    def _rm(self):  # -> ResourceManager[WorkflowRun]
        return self.spec.get_resource_manager(WorkflowRun)

    def _get(self, run_id: str) -> WorkflowRun:
        data = self._rm().get(run_id).data
        assert isinstance(data, WorkflowRun)
        return data

    def _patch(self, run_id: str, **changes: Any) -> None:
        self._rm().update(run_id, msgspec.structs.replace(self._get(run_id), **changes))

    def active_run(self, item_id: str) -> str | None:
        """The item's active run id, or None. Scoped to the item via the indexed
        ``item_id`` query (never a global scan, per the specstar indexing rule)."""
        for r in self._rm().list_resources((QB["item_id"] == item_id).build()):
            data = r.data
            assert isinstance(data, WorkflowRun)
            if data.status in _ACTIVE:
                return r.info.resource_id
        return None

    async def start(self, *, slug: str, item_id: str, profile: str, captured_user: str) -> str:
        """Create a ``WorkflowRun`` (capturing the user, §15) and kick the run off as
        a background task. Rejects a second active run on the same item (§14)."""
        existing = self.active_run(item_id)
        if existing is not None:
            raise ActiveRunExists(item_id, existing)
        manifest = self.load_manifest(slug, profile)
        assert manifest is not None  # the route validated this is a workflow profile
        phases = [PhaseState(phase=p.id) for p in manifest.phases]
        run_id = (
            self._rm()
            .create(WorkflowRun(item_id=item_id, captured_user=captured_user, phases=phases))
            .resource_id
        )
        self._prune_runs(item_id, keep=run_id)
        self._spawn(run_id, slug, item_id, profile, captured_user, manifest)
        return run_id

    def _prune_runs(self, item_id: str, *, keep: str) -> None:
        """Keep at most ``keep_last_runs`` runs per item, pruning the oldest TERMINAL
        ones (active runs + the just-created ``keep`` are never pruned) — manual §16."""
        if not self.keep_last_runs:
            return
        rows: list[tuple[str, WorkflowRun]] = []
        for r in self._rm().list_resources((QB["item_id"] == item_id).build()):
            assert isinstance(r.data, WorkflowRun)
            rows.append((r.info.resource_id, r.data))
        if len(rows) <= self.keep_last_runs:
            return
        # Oldest first; prune terminal, never-keep rows until within the cap.
        rows.sort(key=lambda rd: rd[1].started or 0)
        prunable = sum(1 for _id, d in rows if d.status not in _ACTIVE)
        to_drop = len(rows) - self.keep_last_runs
        for rid, data in rows:
            if to_drop <= 0 or prunable <= 0:
                break
            if rid == keep or data.status in _ACTIVE:
                continue
            self._rm().permanently_delete(rid)
            to_drop -= 1
            prunable -= 1

    def _spawn(
        self,
        run_id: str,
        slug: str,
        item_id: str,
        profile: str,
        captured_user: str,
        manifest: WorkflowManifest,
    ) -> None:
        task = asyncio.create_task(
            self._drive(run_id, slug, item_id, profile, captured_user, manifest)
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._tasks.pop(rid, None))

    async def _drive(
        self,
        run_id: str,
        slug: str,
        item_id: str,
        profile: str,
        captured_user: str,
        manifest: WorkflowManifest,
    ) -> None:
        # Concurrency cap: the run stays `pending` until a slot frees (§16).
        async with self._semaphore():
            await self._execute(run_id, slug, item_id, profile, captured_user, manifest)

    async def _execute(
        self,
        run_id: str,
        slug: str,
        item_id: str,
        profile: str,
        captured_user: str,
        manifest: WorkflowManifest,
    ) -> None:
        wf = self._build_handle(run_id, item_id, captured_user, manifest)
        profile_run = self.load_run(slug, profile)
        inputs = await self._read_inputs(wf, manifest)
        self._step_counts[run_id] = 0
        coro = run_workflow(
            self.spec,
            run_id=run_id,
            profile_run=profile_run,
            wf=wf,
            inputs=inputs,
            now=self.now,
        )
        try:
            if self.run_timeout_s is not None:
                await asyncio.wait_for(coro, self.run_timeout_s)
            else:
                await coro
        except TimeoutError:
            # Wall-clock cap (§17): wait_for cancelled the run; override the driver's
            # `cancelled` with a terminal `error` carrying the reason.
            self._patch(
                run_id,
                status=RunStatus.ERROR,
                ended=self.now(),
                result={"error": f"run exceeded its {self.run_timeout_s}s wall-clock limit"},
            )
        # On a real Stop the inner CancelledError propagates out of here (skipping the
        # post-run step); `cancel()` does the sandbox release for that path.
        await self._post_run(run_id, item_id)

    async def _read_inputs(self, wf: WorkflowHandle, manifest: WorkflowManifest) -> Any:
        """Parsed ``input.json`` (manual §14) — ``{}`` when the file is absent so a
        no-input workflow just runs."""
        if await wf.exists(manifest.input_json):
            return await wf.read_json(manifest.input_json)
        return {}

    def _build_handle(
        self, run_id: str, item_id: str, captured_user: str, manifest: WorkflowManifest | None
    ) -> WorkflowHandle:
        credential = ""
        if self.credentials is not None:
            credential = self.credentials.mint(
                run_id=run_id,
                user=captured_user,
                item_id=item_id,
                ttl_ms=int(self.credential_ttl_s * 1000),
            )
        wf = WorkflowHandle(
            store=self.store,
            workspace_id=item_id,
            config=dict(manifest.config) if manifest is not None else {},
            user=captured_user,
            emit=lambda ev: self._on_event(run_id, item_id, ev),
            credential=credential,
            step_timeout_s=self.step_timeout_s,
        )
        self.wire_handle(wf, run_id, item_id, captured_user)
        return wf

    # ── post-run lifecycle ────────────────────────────────────────────
    async def _post_run(self, run_id: str, item_id: str) -> None:
        data = self._get(run_id)
        status = data.status
        if status is RunStatus.AWAITING_HUMAN and data.pending_decision is not None:
            self.publish(
                item_id,
                AwaitingHumanEvent(
                    phase=data.pending_decision.phase, title=data.pending_decision.title
                ),
            )
        if status is RunStatus.DONE:
            # Mark every entered, non-failed phase passed for the diagram (§12).
            self._patch(
                run_id,
                phases=[
                    msgspec.structs.replace(p, status="passed") if p.status == "running" else p
                    for p in data.phases
                ],
            )
        # Release the sandbox on terminal OR on a (possibly long) human pause (§16).
        # A pause keeps the turn session (stream) alive for the decision card.
        terminal = status in (RunStatus.DONE, RunStatus.ERROR, RunStatus.CANCELLED)
        if terminal and self.credentials is not None:
            self.credentials.revoke(run_id)  # the run-scoped credential dies with the run (§15)
        if terminal or status is RunStatus.AWAITING_HUMAN:
            await self._release(item_id, terminal)
        if status is RunStatus.ERROR and self.notify_failure is not None:
            self.notify_failure(self._get(run_id))

    async def _release(self, item_id: str, terminal: bool) -> None:
        if self.release is not None:
            await self.release(item_id, terminal)

    # ── Stop & take over (§10) ────────────────────────────────────────
    async def cancel(self, run_id: str, item_id: str) -> bool:
        """Stop a run mid-flight: cancel its task (the driver records ``cancelled``),
        then release the sandbox so the item opens to interactive use. Returns False
        when the run isn't running (already terminal / unknown)."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        await self._release(item_id, terminal=True)
        return True

    # ── human gate (§10) ──────────────────────────────────────────────
    async def decide(
        self,
        *,
        slug: str,
        item_id: str,
        profile: str,
        run_id: str,
        choice: str,
        input: str = "",
        decided_by: str = "",
    ) -> None:
        """Record a gate decision as an artifact and resume the run (§10). Re-running
        replays completed steps (they skip, §9); the gate finds the decision and
        continues. Rejects a decision on a run that isn't paused at a gate."""
        data = self._get(run_id)
        if data.status is not RunStatus.AWAITING_HUMAN or data.pending_decision is None:
            raise NotAwaitingDecision(run_id)
        phase = data.pending_decision.phase
        manifest = self.load_manifest(slug, profile)
        wf = self._build_handle(run_id, item_id, data.captured_user, manifest)
        await record_decision(wf, phase=phase, choice=choice, input=input, decided_by=decided_by)
        assert manifest is not None
        self._patch(run_id, status=RunStatus.RUNNING, pending_decision=None)
        self._spawn(run_id, slug, item_id, profile, data.captured_user, manifest)

    # ── event → progress (§12) ────────────────────────────────────────
    def _on_event(self, run_id: str, item_id: str, ev: object) -> None:
        """Map a step event to ``WorkflowRun`` progress + broadcast it on the item's
        stream. Enforces the max-steps budget (§17) by aborting an over-budget run
        before its step executes."""
        if isinstance(ev, StepStarted):
            self._step_counts[run_id] = self._step_counts.get(run_id, 0) + 1
            if self._step_counts[run_id] > self.max_steps:
                raise StepBudgetExceeded(f"exceeded max steps ({self.max_steps})")
        data = self._get(run_id)
        phase = getattr(ev, "phase", "")
        entering = bool(phase) and not isinstance(ev, PhaseEntered) and data.current_phase != phase
        if entering:
            self.publish(item_id, PhaseEntered(phase=phase))
        self.publish(item_id, ev)
        self._apply_progress(run_id, data, ev, entering)

    def _apply_progress(self, run_id: str, data: WorkflowRun, ev: object, entering: bool) -> None:
        phase = getattr(ev, "phase", "")
        phases = list(data.phases)
        idx = next((i for i, p in enumerate(phases) if p.phase == phase), None)
        if idx is None and phase:
            # A step for a phase not in the manifest skeleton — track it anyway (§12
            # caveat: the code may use phases the diagram didn't declare).
            phases.append(PhaseState(phase=phase))
            idx = len(phases) - 1
        if idx is not None:
            p = phases[idx]
            if entering:
                p = msgspec.structs.replace(p, status="running")
            if isinstance(ev, (StepPassed, StepSkipped)):
                p = msgspec.structs.replace(p, done=p.done + 1)
            elif isinstance(ev, StepFailed):
                p = msgspec.structs.replace(p, failed=p.failed + 1, status="failed")
            phases[idx] = p
        changes: dict[str, Any] = {"phases": phases}
        if entering:
            changes["current_phase"] = phase
        self._patch(run_id, **changes)
