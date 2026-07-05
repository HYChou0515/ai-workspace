"""WorkflowOrchestrator — scheduling + supervision around the status driver
(#100, manual §13–§17). Driven directly with fake collaborators (no API layer)."""

import asyncio

import pytest
from specstar import SpecStar

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.checks import file_nonempty
from workspace_app.workflow.engine import CheckResult, run_step
from workspace_app.workflow.events import (
    AwaitingHumanEvent,
    PhaseEntered,
    SteerProposed,
    StepOutput,
    StepSkipped,
)
from workspace_app.workflow.gate import human_gate
from workspace_app.workflow.manifest import WorkflowManifest, WorkflowPhase
from workspace_app.workflow.orchestrator import (
    ActiveRunExists,
    NotAwaitingDecision,
    NotAwaitingSteer,
    WorkflowOrchestrator,
)
from workspace_app.workflow.run import RunStatus, WorkflowRun

MANIFEST = WorkflowManifest(
    phases=[WorkflowPhase(id="think"), WorkflowPhase(id="review")],
    config={"collections": ["a", "b"]},
)


def _clock():
    t = [1000]

    def now() -> int:
        t[0] += 1
        return t[0]

    return now


class _Fakes:
    """Records publishes + releases + notifies for assertions."""

    def __init__(self):
        self.events: list[tuple[str, object]] = []
        self.released: list[tuple[str, bool]] = []
        self.notified: list[WorkflowRun] = []

    def publish(self, key, ev):
        self.events.append((key, ev))

    async def release(self, item_id, terminal, key=None):
        self.released.append((item_id, terminal))

    def notify(self, run):
        self.notified.append(run)

    def types(self):
        return [type(e).__name__ for _i, e in self.events]


def _orch(spec, run_fn, fakes=None, *, store=None, wire=None, now=None, **kw):
    fakes = fakes or _Fakes()
    return (
        WorkflowOrchestrator(
            spec=spec,
            store=store or MemoryFileStore(),
            load_run=lambda _s, _p, _w="": run_fn,
            load_manifest=lambda _s, _p, _w="": MANIFEST,
            wire_handle=wire or (lambda *_a: None),
            publish=fakes.publish,
            release=fakes.release,
            notify_failure=fakes.notify,
            now=now or _clock(),
            **kw,
        ),
        fakes,
    )


def _insert_run(orch, **over):
    """Insert a WorkflowRun row directly (bypassing the driver) so orphan/stuck logic can be
    exercised against a chosen status + progress_at."""
    base: dict = {"item_id": "i", "captured_user": "u", "status": RunStatus.RUNNING}
    base.update(over)
    return orch._rm().create(WorkflowRun(**base)).resource_id


async def _ok(_fb):
    return {"ok": True}


async def test_happy_path_records_done_progress_and_releases(spec_instance: SpecStar):
    async def run(wf, inputs):
        await wf.write_json("out/x.json", {"n": inputs["n"]})
        await run_step(
            wf,
            name="think",
            phase="think",
            args={"a": 1},
            execute=_ok,
            check=file_nonempty("out/x.json"),
        )
        return {"processed": inputs["n"]}

    store = MemoryFileStore()
    # #198: the manifest omits input_json, so the run reads it from the profile's
    # upload_dir (default `uploads`) — `uploads/input.json`, not the old `inputs/`.
    await store.write("rca/i1", "/uploads/input.json", b'{"n": 3}')
    orch, fakes = _orch(spec_instance, run, store=store)
    run_id = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)  # let the background task finish
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.DONE
    assert got.result == {"processed": 3}
    assert got.captured_user == "alice"
    # phase progress: PhaseEntered then StepStarted/Passed; phase marked passed.
    assert "PhaseEntered" in fakes.types() and "StepStarted" in fakes.types()
    think = next(p for p in got.phases if p.phase == "think")
    assert think.status == "passed" and think.done == 1
    assert fakes.released == [("rca/i1", True)]  # sandbox freed on terminal (§16)


async def test_inputs_and_handle_follow_the_profiles_upload_dir(spec_instance: SpecStar):
    """#198: the orchestrator injects the active profile's ``upload_dir`` onto the
    handle and derives the run's ``input.json`` location from it — so a profile that
    stages into ``docs/`` reads ``docs/input.json`` and globs ``docs/*`` without any
    hardcoded folder, and the chat attach (which lands in the same folder) lines up."""
    seen: dict = {}

    async def run(wf, inputs):
        seen["upload_dir"] = wf.upload_dir
        seen["inputs"] = inputs
        return {"ok": True}

    store = MemoryFileStore()
    await store.write("rca/i1", "/docs/input.json", b'{"n": 7}')
    orch, _fakes = _orch(spec_instance, run, store=store, load_upload_dir=lambda _s, _p: "docs")
    run_id = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    assert spec_instance.get_resource_manager(WorkflowRun).get(run_id).data.status is RunStatus.DONE
    assert seen == {"upload_dir": "docs", "inputs": {"n": 7}}


async def test_run_handle_carries_the_per_invocation_identity(spec_instance: SpecStar):
    """#435 P7: the orchestrator exposes each run's per-invocation identity on the handle —
    ``run_id`` (create_new's fresh-per-invocation token, DISTINCT per start) and a resume-
    stable ``run_started_at`` (the run's creation instant, from specstar ``created_time``) —
    so a non-idempotent capability can mint fresh / bucket a window per invocation."""
    from datetime import datetime as _dt

    seen: list[tuple[str, object]] = []

    async def run(wf, inputs):
        seen.append((wf.run_id, wf.run_started_at))
        return {"ok": True}

    store = MemoryFileStore()
    await store.write("rca/i1", "/uploads/input.json", b"{}")
    orch, _f = _orch(spec_instance, run, store=store)
    rid1 = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    rid2 = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    assert [s[0] for s in seen] == [rid1, rid2]  # each run's own id — distinct per invocation
    assert rid1 != rid2
    assert all(isinstance(s[1], _dt) for s in seen)  # resume-stable creation instant


async def test_explicit_input_json_overrides_the_upload_dir_default(spec_instance: SpecStar):
    """#198: a workflow that pins ``input_json`` reads exactly that path, ignoring the
    profile's ``upload_dir`` — the override branch of the derive-from-upload_dir rule."""
    seen: dict = {}

    async def run(wf, inputs):
        seen.update(inputs)
        return {"ok": True}

    store = MemoryFileStore()
    await store.write("rca/i1", "/custom/cfg.json", b'{"n": 5}')
    pinned = WorkflowManifest(phases=[WorkflowPhase(id="think")], input_json="custom/cfg.json")
    orch, _fakes = _orch(spec_instance, run, store=store, load_upload_dir=lambda _s, _p: "uploads")
    orch.load_manifest = lambda _s, _p, _w="": pinned
    run_id = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    assert spec_instance.get_resource_manager(WorkflowRun).get(run_id).data.status is RunStatus.DONE
    assert seen == {"n": 5}


async def test_run_journals_under_its_per_workflow_dir(spec_instance: SpecStar):
    """#136: a run's journaled step artifacts land under /.workflow/<workflow_id>/ —
    the orchestrator threads the run's workflow_id into the handle, so each workflow's
    journal lives in its own folder instead of scattered at the workspace root."""

    async def run(wf, inputs):
        await run_step(wf, name="think", phase="think", args={"a": 1}, execute=_ok)
        return {"ok": True}

    store = MemoryFileStore()
    orch, _fakes = _orch(spec_instance, run, store=store)
    run_id = await orch.start(
        slug="rca", item_id="rca/i1", profile="echo", captured_user="alice", workflow_id="memory"
    )
    await asyncio.sleep(0)
    assert spec_instance.get_resource_manager(WorkflowRun).get(run_id).data.status is RunStatus.DONE
    assert await store.exists("rca/i1", "/.workflow/memory/step_think/main.json")
    assert not await store.exists("rca/i1", "/step_think/main.json")


async def test_step_output_is_streamed_but_not_persisted(spec_instance: SpecStar):
    """#178: live stdout rides the stream as StepOutput but never patches the run —
    a patch per chunk would be a DB write per line, and the journal holds the final
    stdout. So it publishes without inflating phase progress."""

    async def run(wf, inputs):
        wf.emit(StepOutput(phase="think", name="think", text="tick\n"))
        await run_step(wf, name="think", phase="think", args={"a": 1}, execute=_ok)
        return {}

    orch, fakes = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.DONE
    assert "StepOutput" in fakes.types()  # streamed to the FE
    think = next(p for p in got.phases if p.phase == "think")
    assert think.done == 1  # the output chunk did not inflate persisted progress


async def test_distinct_step_persists_a_passed_row_with_duration(spec_instance: SpecStar):
    """#178: a distinct-named step is persisted on WorkflowRun.steps as a 'passed' row
    with server-side started/ended, so the board survives a reload and can show how
    long it took."""

    async def run(wf, inputs):
        await run_step(wf, name="think", phase="think", args={"a": 1}, execute=_ok)
        return {}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    rows = [s for s in got.steps if s.name == "think"]
    assert len(rows) == 1
    s = rows[0]
    assert s.status == "passed"
    assert s.started is not None and s.ended is not None and s.ended >= s.started


async def test_loop_elements_collapse_into_the_counter(spec_instance: SpecStar):
    """#178: same-named loop elements fold into the phase done/total counter instead
    of leaving N step rows — so a 100-file commit phase keeps the board bounded."""

    async def run(wf, inputs):
        for k in ("a", "b", "c"):
            await run_step(wf, name="ingest", key=k, phase="commit", args={"k": k}, execute=_ok)
        return {}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert [s for s in got.steps if s.name == "ingest"] == []  # all collapsed
    commit = next(p for p in got.phases if p.phase == "commit")
    assert commit.done == 3


async def test_a_crashed_step_stays_running_on_the_board(spec_instance: SpecStar):
    """#178 core signal: a step that died mid-flight (its execute crashed) is left on
    the board as 'running' with a start time and no end — that's how you see WHICH
    step was in-flight when the run died, instead of a blank board."""

    async def boom(_fb):
        raise RuntimeError("kaboom")

    async def run(wf, inputs):
        await run_step(wf, name="think", phase="think", args={}, execute=boom)
        return {}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.ERROR
    row = next(s for s in got.steps if s.name == "think")
    assert row.status == "running" and row.started is not None and row.ended is None


async def test_retry_bumps_attempts_on_the_board(spec_instance: SpecStar):
    """#178: a step's retry count is visible — attempts climbs each retry and the
    final 'passed' row records how many tries it took."""
    seen = {"n": 0}

    async def gate(_wf, _result):
        seen["n"] += 1
        return CheckResult(seen["n"] >= 2, "not yet")

    async def run(wf, inputs):
        await run_step(wf, name="think", phase="think", args={}, execute=_ok, check=gate, retries=1)
        return {}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    row = next(s for s in got.steps if s.name == "think")
    assert row.status == "passed" and row.attempts == 2 and row.reason == ""


async def test_failing_distinct_step_keeps_a_failed_row_with_reason(spec_instance: SpecStar):
    """#178: a distinct-named step that aborts persists a 'failed' row carrying why."""

    async def run(wf, inputs):
        await run_step(
            wf, name="think", phase="think", args={}, execute=_ok, check=file_nonempty("missing")
        )
        return {}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="rca/i1", profile="echo", captured_user="alice")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    row = next(s for s in got.steps if s.name == "think")
    assert row.status == "failed" and row.reason and row.ended is not None


async def test_failing_step_records_error_phase_and_notifies(spec_instance: SpecStar):
    async def run(wf, inputs):
        await run_step(
            wf,
            name="think",
            phase="think",
            args={},
            execute=_ok,
            check=file_nonempty("missing.json"),  # never written → fails
        )

    orch, fakes = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="i2", profile="echo", captured_user="bob")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.ERROR
    assert "missing.json" in got.result["error"]  # ty: ignore[not-subscriptable]
    think = next(p for p in got.phases if p.phase == "think")
    assert think.status == "failed" and think.failed == 1
    assert len(fakes.notified) == 1  # in-app failure notification (§17)
    assert fakes.released == [("i2", True)]


async def test_second_active_run_on_same_item_is_rejected(spec_instance: SpecStar):
    gate = asyncio.Event()

    async def run(wf, inputs):
        await gate.wait()
        return {}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="i3", profile="echo", captured_user="u")
    await asyncio.sleep(0)  # let it reach `await gate.wait()` (status running)
    with pytest.raises(ActiveRunExists):
        await orch.start(slug="rca", item_id="i3", profile="echo", captured_user="u")
    gate.set()
    await asyncio.sleep(0)
    # once terminal, a fresh run is allowed (sequential runs per item, §14)
    rid2 = await orch.start(slug="rca", item_id="i3", profile="echo", captured_user="u")
    assert rid2 != run_id


async def test_start_refuses_an_empty_captured_user(spec_instance: SpecStar):
    """#429 E (execution gate 2): a headless/triggered run has no request user, so the acting
    user is threaded through explicitly. If it arrives empty, fail loud — never silently run as
    a system identity (the authz-scope 'no silent errors' rule)."""
    orch, _ = _orch(spec_instance, _run_noop)
    with pytest.raises(ValueError, match="captured_user"):
        await orch.start(slug="rca", item_id="iE", profile="echo", captured_user="")


async def _run_noop(wf, inputs):
    return {}


# ── #429 P8: orphan (stuck-run) detection ────────────────────────────────────


async def test_is_stuck_flags_a_running_run_with_no_recent_progress(spec_instance: SpecStar):
    """#429 P8: a run left RUNNING by a dead pod stops advancing its progress heartbeat, so
    once ``now - progress_at`` passes the grace it reads as stuck (the #227 staleness idiom)."""
    clock = [10_000]
    orch, _ = _orch(spec_instance, _run_noop, now=lambda: clock[0])
    rid = _insert_run(orch, status=RunStatus.RUNNING, started=0, progress_at=1_000)
    clock[0] = 1_000 + 5_000
    assert not orch.is_stuck(rid, grace_ms=10_000)  # 5s < grace → still live
    clock[0] = 1_000 + 20_000
    assert orch.is_stuck(rid, grace_ms=10_000)  # 20s > grace → stuck


async def test_is_stuck_is_false_for_non_running_states(spec_instance: SpecStar):
    """Only a RUNNING run can be a stuck orphan — pending (queued), awaiting_human (a human
    must act), and terminal states are never resumed by the sweeper."""
    orch, _ = _orch(spec_instance, _run_noop, now=lambda: 1_000_000)
    for st in (
        RunStatus.PENDING,
        RunStatus.AWAITING_HUMAN,
        RunStatus.DONE,
        RunStatus.ERROR,
        RunStatus.CANCELLED,
    ):
        rid = _insert_run(orch, status=st, progress_at=0)
        assert not orch.is_stuck(rid, grace_ms=1)


async def test_patch_stamps_progress_at_as_a_heartbeat(spec_instance: SpecStar):
    """Every progress patch stamps ``progress_at`` from the run clock, so a live run's
    heartbeat advances step-by-step and only a dead one goes stale."""
    clock = [500]
    orch, _ = _orch(spec_instance, _run_noop, now=lambda: clock[0])
    rid = _insert_run(orch, status=RunStatus.RUNNING, progress_at=0)
    clock[0] = 777
    orch._patch(rid, current_phase="x")
    assert orch._get(rid).progress_at == 777


# ── #429 P8/F: resume a stuck orphan (CAS-guarded re-drive) ──────────────────


async def test_resume_redrives_a_stuck_orphan_to_completion(spec_instance: SpecStar):
    """A stuck orphan resumes: re-driving replays completed steps (they skip, §9) and the run
    reaches DONE — the crashed run self-heals instead of hanging forever (#429 P8)."""
    ran: list[int] = []

    async def run(wf, inputs):
        ran.append(1)
        return {"ok": True}

    orch, _ = _orch(spec_instance, run, now=lambda: 1_000_000)
    rid = _insert_run(orch, status=RunStatus.RUNNING, started=0, progress_at=0)
    took = await orch.resume(rid, slug="rca", profile="echo", grace_ms=10_000)
    await asyncio.sleep(0)  # let the re-driven task run
    assert took is True
    assert ran == [1]
    assert orch._get(rid).status is RunStatus.DONE


async def test_resume_declines_a_settled_run(spec_instance: SpecStar):
    """A run that reached a terminal state between detection and resume is no longer an orphan
    — resume declines it rather than re-driving a finished run."""
    orch, _ = _orch(spec_instance, _run_noop, now=lambda: 1_000_000)
    rid = _insert_run(orch, status=RunStatus.DONE, progress_at=0)
    assert await orch.resume(rid, slug="rca", profile="echo", grace_ms=1) is False


async def test_resume_is_taken_by_only_one_caller(spec_instance: SpecStar):
    """Two pods detect the same orphan; exactly one takes the resume. The winner stamps a
    fresh heartbeat, so the second call sees it as no-longer-stale and backs off — a run is
    never double-driven (#429 P8/F)."""
    gate = asyncio.Event()
    ran: list[int] = []

    async def run(wf, inputs):
        ran.append(1)
        await gate.wait()
        return {}

    orch, _ = _orch(spec_instance, run, now=lambda: 1_000_000)
    rid = _insert_run(orch, status=RunStatus.RUNNING, started=0, progress_at=0)
    a = await orch.resume(rid, slug="rca", profile="echo", grace_ms=10_000)
    b = await orch.resume(rid, slug="rca", profile="echo", grace_ms=10_000)
    gate.set()
    await asyncio.sleep(0)
    assert [a, b] == [True, False]  # exactly one re-drive
    assert ran == [1]


# ── #429 F-2: abandon a stuck orphan past its resume budget ──────────────────


async def test_abandon_marks_a_run_errored_and_discoverable(spec_instance: SpecStar):
    """#429 F-2: an orphan past its resume budget is abandoned — a one-way move to a terminal,
    DISCOVERABLE state (error + an `abandoned` marker + reason), the sandbox freed and the
    failure surfaced, so it is never silently dropped nor dragged on forever."""
    orch, fakes = _orch(spec_instance, _run_noop, now=lambda: 42)
    rid = _insert_run(orch, item_id="iX", status=RunStatus.RUNNING, progress_at=0)
    await orch.abandon(rid, reason="stuck past its resume budget")
    data = orch._get(rid)
    assert data.status is RunStatus.ERROR
    assert data.result == {"abandoned": True, "reason": "stuck past its resume budget"}
    assert data.ended == 42
    assert ("iX", True) in fakes.released  # sandbox freed
    assert fakes.notified and fakes.notified[-1].result == data.result  # surfaced as a failure


async def test_abandon_is_a_noop_on_a_terminal_run(spec_instance: SpecStar):
    """Abandon is one-way and idempotent — it never rewrites a run that already finished."""
    orch, _ = _orch(spec_instance, _run_noop, now=lambda: 1)
    rid = _insert_run(orch, status=RunStatus.DONE, result={"ok": 1})
    await orch.abandon(rid, reason="x")
    assert orch._get(rid).result == {"ok": 1}  # unchanged


async def test_stop_cancels_and_releases(spec_instance: SpecStar):
    started = asyncio.Event()

    async def run(wf, inputs):
        started.set()
        await asyncio.Event().wait()  # blocks forever until cancelled

    orch, fakes = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="i4", profile="echo", captured_user="u")
    await started.wait()
    assert await orch.cancel(run_id, "i4") is True
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.CANCELLED
    assert fakes.released == [("i4", True)]
    # cancelling an already-terminal run is a no-op
    assert await orch.cancel(run_id, "i4") is False


async def test_human_gate_suspends_then_decision_resumes(spec_instance: SpecStar):
    async def run(wf, inputs):
        d = await human_gate(
            wf, phase="review", title="ok?", summary="plan", allow=["approve", "reject"]
        )
        if d.choice == "reject":
            return {"status": "rejected"}
        return {"status": "approved", "note": d.input}

    orch, fakes = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="i5", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.AWAITING_HUMAN
    assert got.pending_decision is not None and got.pending_decision.phase == "review"
    assert any(isinstance(e, AwaitingHumanEvent) for _i, e in fakes.events)
    assert fakes.released == [("i5", False)]  # sandbox freed during the pause (§16)
    # approve → resume → done
    await orch.decide(
        slug="rca",
        item_id="i5",
        profile="echo",
        run_id=run_id,
        choice="approve",
        input="lgtm",
        decided_by="reviewer",
    )
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.DONE
    assert got.result == {"status": "approved", "note": "lgtm"}


async def test_reviewed_gate_greens_and_prior_phases_green_at_the_pause(spec_instance: SpecStar):
    """#176: the human-reviewed gate phase must light up. While paused, the phases that
    already finished show 'passed' (not stuck 'running'); after approval the gate phase
    itself ends 'passed' (green) instead of reverting to grey."""

    async def run(wf, inputs):
        await run_step(wf, name="think", phase="think", args={}, execute=_ok)
        await human_gate(wf, phase="review", title="ok?", allow=["approve", "reject"])
        return {"status": "approved"}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="iR", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.current_phase == "review"  # the gate is the current phase (slice 1)
    assert next(p for p in got.phases if p.phase == "think").status == "passed"  # 瑕疵2
    assert next(p for p in got.phases if p.phase == "review").status != "passed"  # still awaiting
    # approve → resume → done
    await orch.decide(slug="rca", item_id="iR", profile="echo", run_id=run_id, choice="approve")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.DONE
    assert next(p for p in got.phases if p.phase == "review").status == "passed"  # #176: green
    assert next(p for p in got.phases if p.phase == "think").status == "passed"


async def test_resuming_does_not_replay_a_completed_phase_as_current(spec_instance: SpecStar):
    """#176: on resume the finished phases are skipped — replaying them must NOT
    re-enter them (no PhaseEntered, no backwards current_phase), so the highlight does
    not flicker back to an already-done phase."""

    async def run(wf, inputs):
        await run_step(wf, name="think", phase="think", args={}, execute=_ok)
        await human_gate(wf, phase="review", title="ok?", allow=["approve", "reject"])
        return {"status": "approved"}

    orch, fakes = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="iS", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    fakes.events.clear()  # focus on what the resume publishes
    await orch.decide(slug="rca", item_id="iS", profile="echo", run_id=run_id, choice="approve")
    await asyncio.sleep(0)
    entered = [e.phase for _k, e in fakes.events if isinstance(e, PhaseEntered)]
    assert "think" not in entered  # the skipped, already-finished phase is not re-entered
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.DONE
    assert got.current_phase != "think"  # current_phase never regressed to the skipped phase


async def test_reject_decision_ends_run(spec_instance: SpecStar):
    async def run(wf, inputs):
        d = await human_gate(wf, phase="review", title="ok?", allow=["approve", "reject"])
        return {"status": "rejected"} if d.choice == "reject" else {"status": "approved"}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="i6", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    await orch.decide(slug="rca", item_id="i6", profile="echo", run_id=run_id, choice="reject")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.DONE
    assert got.result == {"status": "rejected"}


async def test_decision_on_non_awaiting_run_is_rejected(spec_instance: SpecStar):
    async def run(wf, inputs):
        return {}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="i7", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    with pytest.raises(NotAwaitingDecision):
        await orch.decide(slug="rca", item_id="i7", profile="echo", run_id=run_id, choice="approve")


async def test_rerun_skips_completed_steps(spec_instance: SpecStar):
    calls = []

    async def run(wf, inputs):
        await wf.write_json("out/x.json", {"n": 1})

        async def ex(_fb):
            calls.append(1)
            return {}

        await run_step(
            wf,
            name="think",
            phase="think",
            args={"a": 1},
            execute=ex,
            check=file_nonempty("out/x.json"),
        )
        return {}

    store = MemoryFileStore()
    orch, fakes = _orch(spec_instance, run, store=store)
    await orch.start(slug="rca", item_id="i8", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    await orch.start(slug="rca", item_id="i8", profile="echo", captured_user="u")  # re-run
    await asyncio.sleep(0)
    assert calls == [1]  # the step executed once; the re-run skipped it
    assert any(isinstance(e, StepSkipped) for _i, e in fakes.events)


async def test_wall_clock_timeout_aborts_to_error(spec_instance: SpecStar):
    async def run(wf, inputs):
        await asyncio.sleep(10)

    orch, fakes = _orch(spec_instance, run, run_timeout_s=0.01)
    run_id = await orch.start(slug="rca", item_id="i9", profile="echo", captured_user="u")
    await asyncio.sleep(0.05)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.ERROR
    assert "wall-clock" in got.result["error"]  # ty: ignore[not-subscriptable]
    assert fakes.released == [("i9", True)]


async def test_max_steps_budget_aborts_to_error(spec_instance: SpecStar):
    async def run(wf, inputs):
        for i in range(5):
            await run_step(wf, name=f"s{i}", phase="think", args={"i": i}, execute=_ok)
        return {}

    orch, _ = _orch(spec_instance, run, max_steps=2)
    run_id = await orch.start(slug="rca", item_id="i10", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.ERROR
    assert "max steps" in got.result["error"]  # ty: ignore[not-subscriptable]


async def test_runs_with_default_collaborators(spec_instance: SpecStar):
    """An orchestrator built with no publish/release/notify (the defaults) still
    drives a run to done — the no-op publish + absent release/notify are inert."""
    from workspace_app.workflow.orchestrator import WorkflowOrchestrator

    async def run(wf, inputs):
        await run_step(wf, name="s", phase="think", args={}, execute=_ok)
        return {"ok": True}

    orch = WorkflowOrchestrator(
        spec=spec_instance,
        store=MemoryFileStore(),
        load_run=lambda _s, _p, _w="": run,
        load_manifest=lambda _s, _p, _w="": MANIFEST,
        wire_handle=lambda *_a: None,
        now=_clock(),
    )
    run_id = await orch.start(slug="rca", item_id="id", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.DONE


async def test_progress_ignores_a_phaseless_step(spec_instance: SpecStar):
    """A step with no phase (run_step default phase="") doesn't enter a phase or
    touch the skeleton — the run still completes."""

    async def run(wf, inputs):
        await run_step(wf, name="x", args={}, execute=_ok)  # no phase=
        return {"ok": True}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="idn", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.DONE
    assert got.current_phase == ""  # a phaseless step never set a current phase


async def test_progress_tracks_a_phase_not_in_the_manifest(spec_instance: SpecStar):
    """A step whose phase the manifest didn't declare is still tracked (the §12
    drift caveat) — appended to the run's phases."""

    async def run(wf, inputs):
        await run_step(wf, name="x", phase="surprise", args={}, execute=_ok)
        return {}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="idp", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert any(p.phase == "surprise" for p in got.phases)


async def test_keep_last_runs_prunes_old_terminal_runs(spec_instance: SpecStar):
    async def run(wf, inputs):
        return {}

    orch, _ = _orch(spec_instance, run, keep_last_runs=2)
    ids = []
    for _ in range(4):
        ids.append(await orch.start(slug="rca", item_id="ik", profile="echo", captured_user="u"))
        await asyncio.sleep(0)  # let each run reach a terminal state before the next
    rm = spec_instance.get_resource_manager(WorkflowRun)
    from specstar import QB

    kept = {
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources((QB["item_id"] == "ik").build())
    }
    assert len(kept) == 2  # only the 2 newest survive (manual §16)
    assert ids[-1] in kept and ids[0] not in kept


async def test_keep_last_one_prunes_down_to_the_active_run(spec_instance: SpecStar):
    async def run(wf, inputs):
        return {}

    orch, _ = _orch(spec_instance, run, keep_last_runs=1)
    await orch.start(slug="rca", item_id="ik1", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    keep = await orch.start(slug="rca", item_id="ik1", profile="echo", captured_user="u")
    from specstar import QB

    rm = spec_instance.get_resource_manager(WorkflowRun)
    ids = [
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources((QB["item_id"] == "ik1").build())
    ]
    assert ids == [keep]  # the prior terminal run was pruned, the new one kept


async def test_prune_keeps_a_terminal_run_a_chat_still_points_at(spec_instance: SpecStar):
    """#343: with same-thread relaunch a chat outlives its run and keeps showing that
    run's result, so retention must never prune a run a live chat still references
    (its ``run_id``) — even a terminal one over the keep_last_runs cap."""
    from specstar import QB

    from workspace_app.resources import Conversation

    async def run(wf, inputs):
        return {}

    orch, _ = _orch(spec_instance, run, keep_last_runs=1)
    rm = spec_instance.get_resource_manager(WorkflowRun)
    conv_rm = spec_instance.get_resource_manager(Conversation)
    _active = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.AWAITING_HUMAN)
    r1 = await orch.start(slug="rca", item_id="ik", profile="echo", captured_user="u", chat_id="cA")
    for _ in range(50):  # let r1 reach a terminal state so it would otherwise be prunable
        await asyncio.sleep(0.01)
        data = rm.get(r1).data
        assert isinstance(data, WorkflowRun)
        if data.status not in _active:
            break
    conv_rm.create(Conversation(item_id="ik", run_id=r1))  # a live chat still points at r1
    conv_rm.create(Conversation(item_id="ik", run_id=None))  # a plain free chat alongside it
    r2 = await orch.start(slug="rca", item_id="ik", profile="echo", captured_user="u", chat_id="cB")
    await asyncio.sleep(0.05)
    ids = {
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources((QB["item_id"] == "ik").build())
    }
    assert r1 in ids  # referenced terminal run survived pruning
    assert r2 in ids


async def test_concurrency_cap_queues_excess(spec_instance: SpecStar):
    release_first = asyncio.Event()

    async def run(wf, inputs):
        if inputs.get("block"):
            await release_first.wait()
        return {}

    store = MemoryFileStore()
    await store.write("c1", "/uploads/input.json", b'{"block": true}')  # #198: derived default
    orch, _ = _orch(spec_instance, run, store=store, concurrency=1)
    r1 = await orch.start(slug="rca", item_id="c1", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    r2 = await orch.start(slug="rca", item_id="c2", profile="echo", captured_user="u")
    await asyncio.sleep(0)
    rm = spec_instance.get_resource_manager(WorkflowRun)
    assert rm.get(r1).data.status is RunStatus.RUNNING
    assert rm.get(r2).data.status is RunStatus.PENDING  # queued behind the cap (§16)
    release_first.set()
    await asyncio.sleep(0.02)  # r1 finishes → frees the slot → r2 acquires + runs
    assert rm.get(r2).data.status is RunStatus.DONE


# ── steer-and-resume (#288 P5) ─────────────────────────────────────────────

_STEER_PLAN = (
    '{"rationale": "switch ingest target",'
    ' "input_edits": [{"path": "collections.json", "content": "new-target"}],'
    ' "invalidate": ["ingest"]}'
)


def _steer_wire(reply: str):
    def wire(wf, *_a):
        async def drive_turn(prompt, tools):
            return reply

        wf.drive_turn = drive_turn

    return wire


def _rec(calls: list, name: str):
    async def execute(_fb):
        calls.append(name)
        return {name: True}

    return execute


async def _ran(orch, run_id, spec):
    await asyncio.sleep(0)
    return spec.get_resource_manager(WorkflowRun).get(run_id).data


async def test_steer_proposes_then_confirm_resumes_incrementally(spec_instance: SpecStar):
    """The heart of #288: a steer edits an input + invalidates a step; on approve the
    run resumes, the still-valid expensive step SKIPS, only the affected step re-runs."""
    calls: list[str] = []

    async def run(wf, inputs):
        await run_step(
            wf, name="expensive", phase="think", args={"x": 1}, execute=_rec(calls, "expensive")
        )
        target = (
            await wf.read_text("collections.json")
            if await wf.exists("collections.json")
            else "none"
        )
        await run_step(
            wf,
            name="ingest",
            phase="review",
            args={"target": target},
            execute=_rec(calls, "ingest"),
        )
        return {"target": target}

    orch, _ = _orch(spec_instance, run, wire=_steer_wire(_STEER_PLAN))
    run_id = await orch.start(slug="rca", item_id="iST", profile="echo", captured_user="u")
    assert (await _ran(orch, run_id, spec_instance)).status is RunStatus.DONE
    assert calls == ["expensive", "ingest"]

    await orch.steer(
        slug="rca",
        item_id="iST",
        profile="echo",
        run_id=run_id,
        instruction="use the new-target collection",
    )
    got = await _ran(orch, run_id, spec_instance)
    assert got.status is RunStatus.AWAITING_HUMAN
    assert got.pending_steer is not None
    assert got.pending_steer.invalidate == ["ingest"]
    assert got.pending_steer.instruction == "use the new-target collection"

    await orch.confirm_steer(
        slug="rca", item_id="iST", profile="echo", run_id=run_id, approve=True, decided_by="alice"
    )
    got = await _ran(orch, run_id, spec_instance)
    assert got.status is RunStatus.DONE
    assert got.pending_steer is None
    assert got.result == {"target": "new-target"}
    assert calls == ["expensive", "ingest", "ingest"]  # expensive skipped; ingest re-ran


async def test_steer_auto_stops_a_running_run_first(spec_instance: SpecStar):
    """A mid-run steer Stops the live run before proposing — otherwise the run task
    still owns the item and the steerer could never suspend it."""
    started = asyncio.Event()

    async def run(wf, inputs):
        started.set()
        await asyncio.Event().wait()  # blocks until cancelled

    orch, fakes = _orch(spec_instance, run, wire=_steer_wire(_STEER_PLAN))
    run_id = await orch.start(slug="rca", item_id="iRun", profile="echo", captured_user="u")
    await started.wait()
    await orch.steer(
        slug="rca", item_id="iRun", profile="echo", run_id=run_id, instruction="redirect"
    )
    got = await _ran(orch, run_id, spec_instance)
    assert got.status is RunStatus.AWAITING_HUMAN  # the live run was Stopped, then steered
    assert got.pending_steer is not None
    assert ("iRun", True) in fakes.released  # the auto-Stop released the sandbox


async def test_reject_steer_on_a_terminal_run_leaves_it_stopped(spec_instance: SpecStar):
    async def run(wf, inputs):
        return {"ok": True}

    orch, _ = _orch(spec_instance, run, wire=_steer_wire(_STEER_PLAN))
    run_id = await orch.start(slug="rca", item_id="iRej", profile="echo", captured_user="u")
    await _ran(orch, run_id, spec_instance)
    await orch.steer(slug="rca", item_id="iRej", profile="echo", run_id=run_id, instruction="x")
    assert (await _ran(orch, run_id, spec_instance)).pending_steer is not None
    await orch.confirm_steer(
        slug="rca", item_id="iRej", profile="echo", run_id=run_id, approve=False
    )
    got = await _ran(orch, run_id, spec_instance)
    assert got.status is RunStatus.CANCELLED
    assert got.pending_steer is None


async def test_reject_steer_at_a_gate_returns_to_the_gate(spec_instance: SpecStar):
    """Steering coexists with a gate: rejecting the steer restores the open gate so the
    human can still approve/reject it (the steer was an optional detour)."""

    async def run(wf, inputs):
        await human_gate(wf, phase="review", title="ok?", allow=["approve", "reject"])
        return {"ok": True}

    orch, _ = _orch(spec_instance, run, wire=_steer_wire(_STEER_PLAN))
    run_id = await orch.start(slug="rca", item_id="iGate", profile="echo", captured_user="u")
    assert (await _ran(orch, run_id, spec_instance)).pending_decision is not None
    await orch.steer(slug="rca", item_id="iGate", profile="echo", run_id=run_id, instruction="x")
    assert (await _ran(orch, run_id, spec_instance)).pending_steer is not None
    await orch.confirm_steer(
        slug="rca", item_id="iGate", profile="echo", run_id=run_id, approve=False
    )
    got = await _ran(orch, run_id, spec_instance)
    assert got.status is RunStatus.AWAITING_HUMAN
    assert got.pending_steer is None
    assert got.pending_decision is not None  # the gate is back


async def test_confirm_steer_without_a_pending_plan_is_rejected(spec_instance: SpecStar):
    async def run(wf, inputs):
        return {}

    orch, _ = _orch(spec_instance, run)
    run_id = await orch.start(slug="rca", item_id="iNo", profile="echo", captured_user="u")
    await _ran(orch, run_id, spec_instance)
    with pytest.raises(NotAwaitingSteer):
        await orch.confirm_steer(
            slug="rca", item_id="iNo", profile="echo", run_id=run_id, approve=True
        )


async def test_failed_steer_proposal_leaves_the_run_stopped_with_a_reason(spec_instance: SpecStar):
    async def run(wf, inputs):
        return {"ok": True}

    orch, _ = _orch(spec_instance, run, wire=_steer_wire("not json at all"))
    run_id = await orch.start(slug="rca", item_id="iFail", profile="echo", captured_user="u")
    await _ran(orch, run_id, spec_instance)
    await orch.steer(slug="rca", item_id="iFail", profile="echo", run_id=run_id, instruction="x")
    got = await _ran(orch, run_id, spec_instance)
    assert got.status is RunStatus.CANCELLED
    assert got.pending_steer is None
    assert "steer_error" in (got.result or {})


async def test_steer_publishes_a_steer_proposed_event(spec_instance: SpecStar):
    """When the steerer has a plan it publishes SteerProposed on the run's stream, so
    the FE shows the confirm card live instead of only on the next poll."""

    async def run(wf, inputs):
        return {"ok": True}

    orch, fakes = _orch(spec_instance, run, wire=_steer_wire(_STEER_PLAN))
    run_id = await orch.start(slug="rca", item_id="iEv", profile="echo", captured_user="u")
    await _ran(orch, run_id, spec_instance)
    fakes.events.clear()
    await orch.steer(
        slug="rca", item_id="iEv", profile="echo", run_id=run_id, instruction="bump it"
    )
    await _ran(orch, run_id, spec_instance)
    proposed = [e for _k, e in fakes.events if isinstance(e, SteerProposed)]
    assert proposed and proposed[0].instruction == "bump it"


async def test_stop_during_a_steer_proposal_settles_the_run_cancelled(spec_instance: SpecStar):
    """If the operator hits Stop while the steerer is still proposing, the run settles
    `cancelled` rather than wedging at `running` (no driver owns the propose task)."""
    blocked = asyncio.Event()

    def wire(wf, *_a):
        async def drive_turn(_prompt, _tools):
            blocked.set()
            await asyncio.Event().wait()  # the steerer hangs until cancelled

        wf.drive_turn = drive_turn

    async def run(wf, inputs):
        return {"ok": True}

    orch, _ = _orch(spec_instance, run, wire=wire)
    run_id = await orch.start(slug="rca", item_id="iStop", profile="echo", captured_user="u")
    await _ran(orch, run_id, spec_instance)
    await orch.steer(slug="rca", item_id="iStop", profile="echo", run_id=run_id, instruction="x")
    await blocked.wait()  # the steerer is mid-proposal
    assert await orch.cancel(run_id, "iStop") is True
    got = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert got.status is RunStatus.CANCELLED
    assert got.pending_steer is None


# ── #323 P4: a workspace-authored workflow shadows the package one ───────────


async def test_workspace_workflow_resolution_shadows_package_and_falls_back(
    spec_instance: SpecStar,
):
    """When ``load_workspace`` resolves a ``.workflows/<id>.json`` for the item, the run
    uses THAT interpreter + manifest (shadowing a same-id package workflow, §22 Q5); when
    it returns None, resolution falls back to the package ``load_run`` / ``load_manifest``."""
    calls: list[str] = []

    async def package_run(_wf, _inputs):
        calls.append("package")
        return {"who": "package"}

    async def workspace_run(_wf, _inputs):
        calls.append("workspace")
        return {"who": "workspace"}

    ws_manifest = WorkflowManifest(phases=[WorkflowPhase(id="ws-phase")])

    async def load_workspace(_item_id, workflow_id):
        return (workspace_run, ws_manifest) if workflow_id == "myflow" else None

    store = MemoryFileStore()
    await store.write("rca/i1", "/uploads/input.json", b"{}")
    await store.write("rca/i2", "/uploads/input.json", b"{}")
    rm = spec_instance.get_resource_manager(WorkflowRun)

    # 1) workspace def present → it wins (run + manifest both from the workspace def)
    orch, _f = _orch(spec_instance, package_run, store=store, load_workspace=load_workspace)
    ws_id = await orch.start(
        slug="rca", item_id="rca/i1", profile="echo", captured_user="alice", workflow_id="myflow"
    )
    await asyncio.sleep(0)
    ws_run = rm.get(ws_id).data
    assert ws_run.status is RunStatus.DONE and ws_run.result == {"who": "workspace"}
    assert [p.phase for p in ws_run.phases] == ["ws-phase"]  # manifest from the workspace def

    # 2) no workspace def for this id → fall back to the package workflow
    pkg_id = await orch.start(
        slug="rca", item_id="rca/i2", profile="echo", captured_user="bob", workflow_id="other"
    )
    await asyncio.sleep(0)
    pkg_run = rm.get(pkg_id).data
    assert pkg_run.status is RunStatus.DONE and pkg_run.result == {"who": "package"}
    assert [p.phase for p in pkg_run.phases] == ["think", "review"]  # the package MANIFEST
    assert calls == ["workspace", "package"]
