"""WorkflowOrchestrator — scheduling + supervision around the status driver
(#100, manual §13–§17). Driven directly with fake collaborators (no API layer)."""

import asyncio

import pytest
from specstar import SpecStar

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.checks import file_nonempty
from workspace_app.workflow.engine import run_step
from workspace_app.workflow.events import (
    AwaitingHumanEvent,
    StepSkipped,
)
from workspace_app.workflow.gate import human_gate
from workspace_app.workflow.manifest import WorkflowManifest, WorkflowPhase
from workspace_app.workflow.orchestrator import (
    ActiveRunExists,
    NotAwaitingDecision,
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

    def publish(self, item_id, ev):
        self.events.append((item_id, ev))

    async def release(self, item_id, terminal):
        self.released.append((item_id, terminal))

    def notify(self, run):
        self.notified.append(run)

    def types(self):
        return [type(e).__name__ for _i, e in self.events]


def _orch(spec, run_fn, fakes=None, *, store=None, **kw):
    fakes = fakes or _Fakes()
    return (
        WorkflowOrchestrator(
            spec=spec,
            store=store or MemoryFileStore(),
            load_run=lambda _s, _p: run_fn,
            load_manifest=lambda _s, _p: MANIFEST,
            wire_handle=lambda *_a: None,
            publish=fakes.publish,
            release=fakes.release,
            notify_failure=fakes.notify,
            now=_clock(),
            **kw,
        ),
        fakes,
    )


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
    await store.write("rca/i1", "/inputs/input.json", b'{"n": 3}')
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
    assert "missing.json" in got.result["error"]
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
    assert "wall-clock" in got.result["error"]
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
    assert "max steps" in got.result["error"]


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
        load_run=lambda _s, _p: run,
        load_manifest=lambda _s, _p: MANIFEST,
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

    kept = {r.info.resource_id for r in rm.list_resources((QB["item_id"] == "ik").build())}
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
    ids = [r.info.resource_id for r in rm.list_resources((QB["item_id"] == "ik1").build())]
    assert ids == [keep]  # the prior terminal run was pruned, the new one kept


async def test_concurrency_cap_queues_excess(spec_instance: SpecStar):
    release_first = asyncio.Event()

    async def run(wf, inputs):
        if inputs.get("block"):
            await release_first.wait()
        return {}

    store = MemoryFileStore()
    await store.write("c1", "/inputs/input.json", b'{"block": true}')
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
