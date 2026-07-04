"""``WorkflowOrchestrator`` — schedules + supervises workflow runs (#100, §13–§17).

The status-only ``driver.run_workflow`` owns one run's terminal status; this owns
everything *around* a run, and is the single object the API routes call:

- builds the run's ``WorkflowHandle`` (wiring drive_turn / run_sandbox / ingest / emit),
- **one run per chat** (topic-hub §3): each run drives its own workflow chat, so many
  may run in parallel on one item; the legacy ``chat_id``-less path keeps the
  one-active-run-per-item rule (§14). A **global concurrency cap** sits excess runs
  ``pending`` until a slot frees (§16),
- maps step events → ``WorkflowRun`` per-phase progress AND broadcasts them on the
  run's chat stream (§12, §3),
- **per-run wall-clock timeout** + **max-steps budget** → ``error`` (§17),
- **releases the sandbox** on terminal / ``awaiting_human`` (§16) + notifies on failure,
- **Stop** (cancel, §10) and the **human-gate decide → resume** cycle (§10).

It is deliberately App-agnostic: the API layer injects ``load_run`` / ``load_manifest``
(profile + workflow discovery), ``wire_handle`` (attach the turn/sandbox/ingest
drivers, keyed on the run's chat), ``publish`` (broadcast on the run's chat stream),
``release`` (tear the sandbox down) and ``notify_failure``. ``chat_id`` is just an
opaque stream/turn key here — the Conversation overlay lives in the API layer. Tests
inject fakes and drive it directly.
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
    SteerProposed,
    StepFailed,
    StepOutput,
    StepPassed,
    StepRetrying,
    StepSkipped,
    StepStarted,
)
from .gate import record_decision
from .handle import WorkflowHandle
from .inputs import resolve_inputs
from .manifest import WorkflowManifest
from .run import PhaseState, RunStatus, StepState, WorkflowRun
from .steer import SteerProposalFailed, apply_steer, propose_steer

# A run is "active" (blocks a second start, manual §14) while pending / running /
# paused at a gate. The rest are terminal.
_ACTIVE = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.AWAITING_HUMAN)

ProfileRun = Callable[[WorkflowHandle, Any], Awaitable[Any]]
# Attach the run's leaf drivers (drive_turn / run_sandbox / ingest) to a freshly
# built handle: (wf, run_id, item_id, captured_user, chat_key). ``chat_key`` is the
# run's stream/turn key (its workflow chat, or item_id legacy) — drive_turn enqueues
# + persists there; run_sandbox/ingest stay on item_id (the shared workspace).
WireHandle = Callable[[WorkflowHandle, str, str, str, str], None]


class ActiveRunExists(Exception):
    """A second run was started on an item that already has an active one (§14)."""

    def __init__(self, item_id: str, run_id: str) -> None:
        self.item_id = item_id
        self.run_id = run_id
        super().__init__(f"item {item_id!r} already has an active run ({run_id})")


class NotAwaitingDecision(Exception):
    """A decision was posted to a run that is not paused at a gate (§10)."""


class NotAwaitingSteer(Exception):
    """A steer confirm was posted to a run with no steer plan pending (#288, §10)."""


class StepBudgetExceeded(Exception):
    """The run blew its max-steps ceiling — guards runaway loops (manual §17)."""


def _noop_publish(_item_id: str, _event: Any) -> None:
    pass


def _default_upload_dir(_slug: str, _profile: str) -> str:
    """Fallback profile staging folder when the API layer injects no resolver (#198)."""
    return "uploads"


@dataclass
class WorkflowOrchestrator:
    spec: SpecStar
    store: FileStore
    load_run: Callable[[str, str, str], ProfileRun]
    load_manifest: Callable[[str, str, str], WorkflowManifest | None]
    wire_handle: WireHandle
    # (chat_key, event) — broadcast a phase/step event on the run's stream (its
    # workflow chat, or the item's broadcast stream legacy). Typed ``Any`` event so
    # the API can pass ``turn_engine.publish`` (narrower AgentEvent union) cleanly.
    publish: Callable[[str, Any], None] = _noop_publish
    # #198: resolve the active profile's staging folder — ``(slug, profile) → upload_dir``.
    # Injected from ``load_profile(...).upload_dir``; the handle carries it (``wf.upload_dir``)
    # and the run's ``input.json`` location derives from it. Default ⇒ ``uploads``.
    load_upload_dir: Callable[[str, str], str] = _default_upload_dir
    # #323 P4: resolve a WORKSPACE-authored workflow — a ``.workflows/<id>.json`` in the
    # item's FileStore — to its ``(run, manifest)``, shadowing a package workflow of the
    # same id (manual §22, Q5). ``(item_id, workflow_id) → (run, manifest) | None``; async
    # because the FileStore read is. Returns generic types so the orchestrator stays
    # decoupled from the DSL (the API injects the dsl-aware closure). None ⇒ package-only
    # (the default — existing callers/tests are unchanged; resolution falls back to
    # ``load_run`` / ``load_manifest``).
    load_workspace: (
        Callable[[str, str], Awaitable[tuple[ProfileRun, WorkflowManifest] | None]] | None
    ) = None
    # Release the run's resources (manual §16): ``release(item_id, terminal, chat_key)``.
    # ``terminal`` is True on done/error/cancelled (tear down sandbox + the run's turn
    # session), False on an ``awaiting_human`` pause (free the sandbox but keep the
    # stream alive for the decision card). ``chat_key`` identifies the run's chat/turn
    # session. Injected by the API layer; None ⇒ no-op.
    release: Callable[[str, bool, str], Awaitable[None]] | None = None
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

    async def _resolve_manifest(
        self, slug: str, profile: str, workflow_id: str, item_id: str
    ) -> WorkflowManifest | None:
        """The run's manifest — a workspace ``.workflows/<id>.json`` (shadowing a package
        workflow of the same id, §22/Q5) if one is wired + present, else the package
        manifest. Re-resolved on every reach (start + each resume) so an edited def is
        picked up, like the §9 file-as-journal model."""
        if self.load_workspace is not None:
            ws = await self.load_workspace(item_id, workflow_id)
            if ws is not None:
                return ws[1]
        return self.load_manifest(slug, profile, workflow_id)

    async def _resolve_run(
        self, slug: str, profile: str, workflow_id: str, item_id: str
    ) -> ProfileRun:
        """The run's ``run()`` — the interpreter over a workspace ``.workflows/<id>.json``
        if wired + present, else the package ``run.py`` / package DSL (§22/Q5)."""
        if self.load_workspace is not None:
            ws = await self.load_workspace(item_id, workflow_id)
            if ws is not None:
                return ws[0]
        return self.load_run(slug, profile, workflow_id)

    def active_run(self, item_id: str) -> str | None:
        """The item's active run id, or None. Scoped to the item via the indexed
        ``item_id`` query (never a global scan, per the specstar indexing rule)."""
        for r in self._rm().list_resources((QB["item_id"] == item_id).build()):
            data = r.data
            assert isinstance(data, WorkflowRun)
            if data.status in _ACTIVE:
                return r.info.resource_id
        return None

    def active_run_for_chat(self, item_id: str, chat_id: str) -> str | None:
        """The active run driving ``chat_id`` (a specific thread), or None. #343: a
        chat hosts one run at a time — a takeover launch checks this so a thread whose
        run is still live can't start a second one (a terminal one is fine). Scoped to
        the item via the indexed ``item_id`` query, then filtered by chat."""
        for r in self._rm().list_resources((QB["item_id"] == item_id).build()):
            data = r.data
            assert isinstance(data, WorkflowRun)
            if data.chat_id == chat_id and data.status in _ACTIVE:
                return r.info.resource_id
        return None

    async def start(
        self,
        *,
        slug: str,
        item_id: str,
        profile: str,
        captured_user: str,
        workflow_id: str = "",
        chat_id: str = "",
    ) -> str:
        """Create a ``WorkflowRun`` (capturing the user, §15) and kick the run off as
        a background task. ``workflow_id`` selects which of the profile's workflows to
        run (manual §4); ``chat_id`` is the workflow chat it drives (manual §3). With a
        ``chat_id``, runs are **per-chat** so many may run in parallel on one item
        (§3); without one (legacy), the one-active-run-per-item rule still holds (§14)."""
        # #429 E (execution gate 2): a headless (triggered) run has no request user, so the
        # acting user must be threaded through explicitly. If it arrives empty — a plumbing
        # bug, a lost serialization — FAIL LOUD rather than silently run as a system/superuser
        # (the authz-scope version of the 'no silent errors' rule). A declaration-time check
        # (validate_triggers) already rejects an empty acting_user; this defends the run point.
        if not captured_user:
            raise ValueError(
                "workflow run needs a non-empty captured_user (the acting authz scope) — "
                "refusing to run headless with no identity"
            )
        # Without a chat_id the one-active-run-per-item rule holds (§14); with one, runs
        # are per-chat, so guard only the target chat (#343 takeover / topic-hub §3).
        existing = (
            self.active_run_for_chat(item_id, chat_id) if chat_id else self.active_run(item_id)
        )
        if existing is not None:
            raise ActiveRunExists(item_id, existing)
        manifest = await self._resolve_manifest(slug, profile, workflow_id, item_id)
        assert manifest is not None  # the route validated this is a workflow profile
        phases = [PhaseState(phase=p.id) for p in manifest.phases]
        run_id = (
            self._rm()
            .create(
                WorkflowRun(
                    item_id=item_id,
                    captured_user=captured_user,
                    phases=phases,
                    chat_id=chat_id,
                    workflow_id=workflow_id,
                )
            )
            .resource_id
        )
        self._prune_runs(item_id, keep=run_id)
        self._spawn(run_id, slug, item_id, profile, captured_user, manifest, workflow_id, chat_id)
        return run_id

    def _chat_referenced_runs(self, item_id: str) -> set[str]:
        """Run ids a live chat still points at (its ``run_id``). #343: with same-thread
        relaunch a chat outlives its run and keeps showing that run's result, so a
        referenced run is pinned against retention pruning."""
        from ..resources import Conversation

        conv_rm = self.spec.get_resource_manager(Conversation)
        out: set[str] = set()
        for r in conv_rm.list_resources((QB["item_id"] == item_id).build()):
            data = r.data
            assert isinstance(data, Conversation)
            if data.run_id:
                out.add(data.run_id)
        return out

    def _prune_runs(self, item_id: str, *, keep: str) -> None:
        """Keep at most ``keep_last_runs`` runs per item, pruning the oldest TERMINAL
        ones (active runs, the just-created ``keep``, and any a live chat still points
        at are never pruned) — manual §16 + #343."""
        if not self.keep_last_runs:
            return
        rows: list[tuple[str, WorkflowRun]] = []
        for r in self._rm().list_resources((QB["item_id"] == item_id).build()):
            assert isinstance(r.data, WorkflowRun)
            rows.append((r.info.resource_id, r.data))
        if len(rows) <= self.keep_last_runs:
            return
        pinned = self._chat_referenced_runs(item_id)
        # Oldest first; prune terminal, unpinned, never-keep rows until within the cap.
        rows.sort(key=lambda rd: rd[1].started or 0)
        prunable = sum(1 for rid, d in rows if d.status not in _ACTIVE and rid not in pinned)
        to_drop = len(rows) - self.keep_last_runs
        for rid, data in rows:
            if to_drop <= 0 or prunable <= 0:
                break
            if rid == keep or data.status in _ACTIVE or rid in pinned:
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
        workflow_id: str,
        chat_id: str,
    ) -> None:
        self._track(
            run_id,
            asyncio.create_task(
                self._drive(
                    run_id, slug, item_id, profile, captured_user, manifest, workflow_id, chat_id
                )
            ),
        )

    def _track(self, run_id: str, task: asyncio.Task[None]) -> None:
        """Register a run's in-flight task, replacing any prior one (a resume / steer
        re-spawns under the same ``run_id``). The done-callback removes the task only if
        it is STILL the registered one — so a finished task's late callback can't evict
        the successor that a resume/steer just installed (the cause of a lost handle)."""
        self._tasks[run_id] = task

        def _untrack(t: asyncio.Task[None], rid: str = run_id) -> None:
            if self._tasks.get(rid) is t:
                del self._tasks[rid]

        task.add_done_callback(_untrack)

    async def _drive(
        self,
        run_id: str,
        slug: str,
        item_id: str,
        profile: str,
        captured_user: str,
        manifest: WorkflowManifest,
        workflow_id: str,
        chat_id: str,
    ) -> None:
        # Concurrency cap: the run stays `pending` until a slot frees (§16).
        async with self._semaphore():
            await self._execute(
                run_id, slug, item_id, profile, captured_user, manifest, workflow_id, chat_id
            )

    async def _execute(
        self,
        run_id: str,
        slug: str,
        item_id: str,
        profile: str,
        captured_user: str,
        manifest: WorkflowManifest,
        workflow_id: str,
        chat_id: str,
    ) -> None:
        key = chat_id or item_id
        upload_dir = self.load_upload_dir(slug, profile)
        wf = self._build_handle(
            run_id, item_id, captured_user, manifest, key, workflow_id, upload_dir
        )
        profile_run = await self._resolve_run(slug, profile, workflow_id, item_id)
        inputs = await resolve_inputs(wf, manifest)
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
        await self._post_run(run_id, item_id, key)

    def _build_handle(
        self,
        run_id: str,
        item_id: str,
        captured_user: str,
        manifest: WorkflowManifest | None,
        key: str,
        workflow_id: str = "",
        upload_dir: str = "uploads",
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
            workflow_id=workflow_id,
            config=dict(manifest.config) if manifest is not None else {},
            upload_dir=upload_dir,
            user=captured_user,
            emit=lambda ev: self._on_event(run_id, key, ev),
            credential=credential,
            step_timeout_s=self.step_timeout_s,
        )
        self.wire_handle(wf, run_id, item_id, captured_user, key)
        return wf

    # ── post-run lifecycle ────────────────────────────────────────────
    async def _post_run(self, run_id: str, item_id: str, key: str) -> None:
        data = self._get(run_id)
        status = data.status
        if status is RunStatus.AWAITING_HUMAN and data.pending_decision is not None:
            # Green the phases that finished before the gate (#176): without this they
            # stay 'running' (blue) until the run reaches DONE, so a pause looks like
            # work is still in flight. The gate's own phase is left untouched — it is
            # not done yet, and the FE overlays it as the awaiting node anyway.
            gate = data.pending_decision.phase
            self._patch(
                run_id,
                phases=[
                    msgspec.structs.replace(p, status="passed")
                    if p.status == "running" and p.phase != gate
                    else p
                    for p in data.phases
                ],
            )
            self.publish(
                key,
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
            await self._release(item_id, terminal, key)
        if status is RunStatus.ERROR and self.notify_failure is not None:
            self.notify_failure(self._get(run_id))

    async def _release(self, item_id: str, terminal: bool, key: str) -> None:
        if self.release is not None:
            await self.release(item_id, terminal, key)

    # ── Stop & take over (§10) ────────────────────────────────────────
    async def cancel(self, run_id: str, item_id: str) -> bool:
        """Stop a run mid-flight: cancel its task (the driver records ``cancelled``),
        then release the sandbox so the item opens to interactive use. Returns False
        when the run isn't running (already terminal / unknown)."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        key = self._get(run_id).chat_id or item_id
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        await self._release(item_id, terminal=True, key=key)
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
        key = data.chat_id or item_id
        manifest = await self._resolve_manifest(slug, profile, data.workflow_id, item_id)
        wf = self._build_handle(
            run_id,
            item_id,
            data.captured_user,
            manifest,
            key,
            data.workflow_id,
            self.load_upload_dir(slug, profile),
        )
        await record_decision(wf, phase=phase, choice=choice, input=input, decided_by=decided_by)
        assert manifest is not None
        self._patch(run_id, status=RunStatus.RUNNING, pending_decision=None)
        self._spawn(
            run_id,
            slug,
            item_id,
            profile,
            data.captured_user,
            manifest,
            data.workflow_id,
            data.chat_id,
        )

    # ── steer-and-resume (#288, §10) ──────────────────────────────────
    async def steer(
        self, *, slug: str, item_id: str, profile: str, run_id: str, instruction: str
    ) -> None:
        """Begin a conversational steer (#288): if the run is still going, **Stop** it
        first (the in-flight node re-runs on resume anyway), then run the read-only
        steerer in the background. It streams into the run's chat and, when it has a
        plan, suspends the run ``awaiting_human`` with ``pending_steer`` set for the
        human to confirm. A failed proposal leaves the run ``cancelled``."""
        data = self._get(run_id)
        if data.status in (RunStatus.RUNNING, RunStatus.PENDING):
            await self.cancel(run_id, item_id)
        self._patch(run_id, status=RunStatus.RUNNING)  # the steerer is working
        self._track(
            run_id,
            asyncio.create_task(self._propose(run_id, slug, item_id, profile, instruction, data)),
        )

    async def _propose(
        self,
        run_id: str,
        slug: str,
        item_id: str,
        profile: str,
        instruction: str,
        data: WorkflowRun,
    ) -> None:
        key = data.chat_id or item_id
        manifest = await self._resolve_manifest(slug, profile, data.workflow_id, item_id)
        wf = self._build_handle(
            run_id,
            item_id,
            data.captured_user,
            manifest,
            key,
            data.workflow_id,
            self.load_upload_dir(slug, profile),
        )
        try:
            plan = await propose_steer(wf, instruction=instruction)
        except SteerProposalFailed as exc:
            # Couldn't form a usable plan — leave the run stopped with the reason so the
            # operator can re-instruct or take over.
            self._patch(
                run_id,
                status=RunStatus.CANCELLED,
                pending_steer=None,
                result={"steer_error": str(exc)},
            )
            return
        except asyncio.CancelledError:
            # Stopped mid-proposal (the operator hit Stop while the steerer ran) — settle
            # the run as cancelled instead of leaving it wedged `running` (no driver owns
            # this task to record the terminal status).
            self._patch(run_id, status=RunStatus.CANCELLED, pending_steer=None)
            raise
        self._patch(run_id, status=RunStatus.AWAITING_HUMAN, pending_steer=plan)
        self.publish(key, SteerProposed(instruction=plan.instruction, rationale=plan.rationale))

    async def confirm_steer(
        self,
        *,
        slug: str,
        item_id: str,
        profile: str,
        run_id: str,
        approve: bool,
        decided_by: str = "",
    ) -> None:
        """Resolve a pending steer (#288). **Approve** → apply the edits + invalidate
        the steps (deterministically), then **resume the same run** (§9 re-run skips the
        valid prefix). **Reject** → discard the plan; the run returns to its gate (if it
        was paused at one) or to a stopped state. Rejects a confirm with no plan pending."""
        data = self._get(run_id)
        if data.pending_steer is None:
            raise NotAwaitingSteer(run_id)
        key = data.chat_id or item_id
        manifest = await self._resolve_manifest(slug, profile, data.workflow_id, item_id)
        if not approve:
            restored = (
                RunStatus.AWAITING_HUMAN
                if data.pending_decision is not None
                else RunStatus.CANCELLED
            )
            self._patch(run_id, status=restored, pending_steer=None)
            return
        wf = self._build_handle(
            run_id,
            item_id,
            data.captured_user,
            manifest,
            key,
            data.workflow_id,
            self.load_upload_dir(slug, profile),
        )
        await apply_steer(wf, data.pending_steer, decided_by=decided_by)
        assert manifest is not None
        self._patch(run_id, status=RunStatus.RUNNING, pending_steer=None, pending_decision=None)
        self._spawn(
            run_id,
            slug,
            item_id,
            profile,
            data.captured_user,
            manifest,
            data.workflow_id,
            data.chat_id,
        )

    # ── event → progress (§12) ────────────────────────────────────────
    def _on_event(self, run_id: str, key: str, ev: object) -> None:
        """Map a step event to ``WorkflowRun`` progress + broadcast it on the run's
        stream (``key`` = its workflow chat, or the item's broadcast stream legacy).
        Enforces the max-steps budget (§17) by aborting an over-budget run before its
        step executes."""
        if isinstance(ev, StepOutput):
            # Live stdout (#178) is ephemeral: stream it only, never read/patch the
            # resource (a patch per chunk would be a DB write per stdout line).
            self.publish(key, ev)
            return
        if isinstance(ev, StepStarted):
            self._step_counts[run_id] = self._step_counts.get(run_id, 0) + 1
            if self._step_counts[run_id] > self.max_steps:
                raise StepBudgetExceeded(f"exceeded max steps ({self.max_steps})")
        data = self._get(run_id)
        phase = getattr(ev, "phase", "")
        # A skip means the phase already ran in an earlier pass (a resume replays the
        # completed prefix, §9) — it must NOT count as *entering* the phase, or the
        # highlight regresses to an already-finished phase and re-marks it 'running'
        # (#176). Only genuine work (StepStarted/Passed/…) advances the current phase.
        entering = (
            bool(phase)
            and not isinstance(ev, (PhaseEntered, StepSkipped))
            and data.current_phase != phase
        )
        if entering:
            self.publish(key, PhaseEntered(phase=phase))
        self.publish(key, ev)
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
        changes: dict[str, Any] = {"phases": phases, "steps": self._apply_step_record(data, ev)}
        if entering:
            changes["current_phase"] = phase
        self._patch(run_id, **changes)

    def _apply_step_record(self, data: WorkflowRun, ev: object) -> list[StepState]:
        """Maintain the bounded per-step board (#178). A step is keyed by
        (phase, name, key). Loop elements (``key != ""``) live here only while running
        and drop on terminal (folded into the phase counter / ``failures``); distinct
        named steps (``key == ""``) persist with their final status + duration."""
        if not isinstance(ev, (StepStarted, StepRetrying, StepPassed, StepSkipped, StepFailed)):
            return data.steps
        phase, name, key = ev.phase, ev.name, ev.key

        def same(s: StepState) -> bool:
            return s.phase == phase and s.name == name and s.key == key

        steps = [s for s in data.steps if not same(s)]
        prior = next((s for s in data.steps if same(s)), None)
        if isinstance(ev, StepStarted):
            steps.append(StepState(phase=phase, name=name, key=key, started=self.now()))
        elif isinstance(ev, StepRetrying):
            base = prior or StepState(phase=phase, name=name, key=key, started=self.now())
            steps.append(
                msgspec.structs.replace(
                    base, status="retrying", attempts=base.attempts + 1, reason=ev.reason
                )
            )
        elif key:
            # A loop element reached a terminal state — collapse it into the counter
            # (skip re-appending; failures already track failed elements, §11).
            pass
        elif isinstance(ev, StepFailed):
            base = prior or StepState(phase=phase, name=name, key=key)
            steps.append(
                msgspec.structs.replace(base, status="failed", ended=self.now(), reason=ev.reason)
            )
        else:  # StepPassed / StepSkipped — terminal success, clear any retry reason
            status = "skipped" if isinstance(ev, StepSkipped) else "passed"
            base = prior or StepState(phase=phase, name=name, key=key)
            steps.append(msgspec.structs.replace(base, status=status, ended=self.now(), reason=""))
        return steps
