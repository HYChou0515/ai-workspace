"""human_gate (manual §10) — the decision-as-artifact mechanism: suspend on first
reach, resume by re-running once a decision is recorded."""

import pytest
from specstar import SpecStar

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.driver import run_workflow
from workspace_app.workflow.events import StepPassed, StepStarted
from workspace_app.workflow.gate import AwaitingHuman, human_gate, record_decision
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.run import RunStatus, WorkflowRun


async def test_gate_suspends_when_no_decision_recorded(wf: WorkflowHandle):
    with pytest.raises(AwaitingHuman) as ei:
        await human_gate(wf, phase="review", title="Export?", summary={"files": 3})
    assert ei.value.phase == "review"
    assert ei.value.title == "Export?"
    assert ei.value.allow == ["approve", "reject"]
    assert '"files": 3' in ei.value.summary  # dict summary is stringified


async def test_gate_returns_the_recorded_decision(wf: WorkflowHandle):
    await record_decision(wf, phase="review", choice="approve", input="ship it")
    decision = await human_gate(wf, phase="review", title="Export?")
    assert decision.choice == "approve"
    assert decision.input == "ship it"


async def test_gate_emits_step_started_while_awaiting():
    """#176: the gate is a step too — reaching it (no decision yet) emits StepStarted
    so the phase enters the run's progress (a yellow, *current* node), instead of
    being invisible until the FE overlays the pending-decision. No StepPassed yet."""
    events: list[object] = []
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", emit=events.append)
    with pytest.raises(AwaitingHuman):
        await human_gate(wf, phase="review", title="ok?")
    assert any(isinstance(e, StepStarted) and e.phase == "review" for e in events)
    assert not any(isinstance(e, StepPassed) for e in events)


async def test_gate_emits_step_passed_once_a_decision_exists():
    """#176: the reviewed gate must light up green — reaching it with a recorded
    decision emits StepPassed for its phase, so the phase ends 'passed' instead of
    reverting to grey once the pending-decision overlay disappears."""
    events: list[object] = []
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", emit=events.append)
    await record_decision(wf, phase="review", choice="approve")
    events.clear()
    decision = await human_gate(wf, phase="review", title="ok?")
    assert decision.choice == "approve"
    assert any(isinstance(e, StepPassed) and e.phase == "review" for e in events)


async def test_decision_artifact_lives_under_per_workflow_dir():
    """#136: the gate decision is a journal artifact, so it lives under the run's
    /.workflow/<workflow_id>/ folder too — not scattered at the workspace root."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", workflow_id="collections")
    await record_decision(wf, phase="review", choice="approve")
    assert await wf.exists("/.workflow/collections/step_review/decision.json")
    assert not await wf.exists("/step_review/decision.json")


def _run(spec: SpecStar) -> str:
    return (
        spec.get_resource_manager(WorkflowRun)
        .create(WorkflowRun(item_id="rca/a/1", captured_user="alice"))
        .resource_id
    )


async def test_driver_marks_awaiting_human_with_the_pending_decision(spec_instance: SpecStar):
    rid = _run(spec_instance)
    store = MemoryFileStore()
    wf = WorkflowHandle(store=store, workspace_id="ws")

    async def run(w, inputs):
        await human_gate(w, phase="review", title="Approve export?", summary="the report")
        return {"status": "exported"}  # not reached on the first pass

    await run_workflow(spec_instance, run_id=rid, profile_run=run, wf=wf, inputs={})
    got = spec_instance.get_resource_manager(WorkflowRun).get(rid).data
    assert got.status is RunStatus.AWAITING_HUMAN
    assert got.pending_decision is not None
    assert got.pending_decision.phase == "review"
    assert got.pending_decision.title == "Approve export?"


async def test_recording_a_decision_then_rerunning_resumes_to_done(spec_instance: SpecStar):
    """The full §10 loop: suspend → human decides → re-run finds the decision
    artifact at the gate and continues to a terminal status."""
    rid = _run(spec_instance)
    store = MemoryFileStore()
    wf = WorkflowHandle(store=store, workspace_id="ws")

    async def run(w, inputs):
        decision = await human_gate(w, phase="review", title="Approve?")
        return {"status": "exported" if decision.choice == "approve" else "handed_off"}

    await run_workflow(spec_instance, run_id=rid, profile_run=run, wf=wf, inputs={})
    assert spec_instance.get_resource_manager(WorkflowRun).get(rid).data.status is (
        RunStatus.AWAITING_HUMAN
    )

    await record_decision(wf, phase="review", choice="approve")
    await run_workflow(spec_instance, run_id=rid, profile_run=run, wf=wf, inputs={})
    got = spec_instance.get_resource_manager(WorkflowRun).get(rid).data
    assert got.status is RunStatus.DONE
    assert got.result == {"status": "exported"}
