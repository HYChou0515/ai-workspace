"""At most one phase is running: the one the execution is in (plan-one-running-phase).

The execution tab draws a row per phase, streams the live turn in the running
one and keeps a running row open. Phases used to be marked running on entry
and never left it until the run reached a gate or DONE, so mid-run every phase
walked so far read running at once — the next step streamed under the first
one's row, which could not be collapsed; after an error or a Stop they stayed
running for good. Driven end to end: a real workflow over the HTTP API, the
stored run read back as the orchestrator wrote it.
"""

import asyncio
import json
import threading
import time
from datetime import timedelta
from typing import Any

from specstar import SpecStar

from tests.api._client import TestClient
from workspace_app.api import MessageDelta, RunDone, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.workflow.run import PhaseState, RunStatus, WorkflowRun

FLOW = {
    "id": "flow",
    "title": "Two steps",
    "phases": [{"id": "first", "title": "Step one"}, {"id": "second", "title": "Step two"}],
    "steps": [
        {
            "type": "agent",
            "cache": False,
            "name": "s1",
            "phase": "first",
            "prompt": "STEP1",
            "out": "a.md",
        },
        {
            "type": "agent",
            "cache": False,
            "name": "s2",
            "phase": "second",
            "prompt": "STEP2",
            "out": "b.md",
        },
    ],
}


class _HoldStepTwo:
    """Answers step 1 at once and holds step 2 open until released."""

    def __init__(self) -> None:
        self.in_step_two = threading.Event()
        self.release = threading.Event()

    async def run(self, prompt: str, ctx: Any):  # noqa: ANN201
        if prompt.startswith("STEP2"):
            yield MessageDelta(text="working on two ")
            self.in_step_two.set()
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        yield MessageDelta(text="done")
        yield RunDone()


class _Item:
    def __init__(self, runner: _HoldStepTwo, flow: dict = FLOW, **app_kw: Any) -> None:
        self.spec: SpecStar = make_spec()
        self.runner = runner
        app = create_app(
            spec=self.spec,
            sandbox=MockSandbox(),
            filestore=SpecstarFileStore(self.spec),
            runner=runner,
            **app_kw,
        )
        item = (
            self.spec.get_resource_manager(PlaygroundItem)
            .create(PlaygroundItem(title="t", owner="u", profile="default"))
            .resource_id
        )
        self.base = f"/a/playground/items/{item}"
        self.client = TestClient(app)
        self.flow = flow

    def launch(self) -> str:
        put = self.client.put(
            f"{self.base}/files/.workflows/flow.json", content=json.dumps(self.flow)
        )
        assert put.status_code == 204
        run_id = self.client.post(f"{self.base}/run?workflow_id=flow").json()["run_id"]
        assert self.runner.in_step_two.wait(10)
        return run_id

    def stored(self, run_id: str) -> dict[str, str]:
        """The phases as the orchestrator WROTE them (not as a route serves them)."""
        run = self.spec.get_resource_manager(WorkflowRun).get(run_id).data
        assert isinstance(run, WorkflowRun)
        return {p.phase: p.status for p in run.phases}

    def settle(self, run_id: str) -> str:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = self.client.get(f"{self.base}/runs/{run_id}").json()["status"]
            if status in ("done", "error", "cancelled"):
                time.sleep(0.1)  # the post-run patch lands just after the status
                return status
            time.sleep(0.02)
        raise AssertionError("the run never ended")


def test_mid_run_only_the_phase_it_is_in_is_running():
    item = _Item(_HoldStepTwo())
    with item.client:
        run_id = item.launch()
        mid = item.stored(run_id)
        item.runner.release.set()
        item.settle(run_id)
    assert mid == {"first": "passed", "second": "running"}


def test_a_stop_leaves_the_phase_it_was_in_stopped_and_the_rest_passed():
    item = _Item(_HoldStepTwo())
    with item.client:
        run_id = item.launch()
        assert item.client.post(f"{item.base}/runs/{run_id}/cancel").status_code == 204
        item.runner.release.set()
        assert item.settle(run_id) == "cancelled"
        stored = item.stored(run_id)
    assert stored == {"first": "passed", "second": "stopped"}


def test_an_error_leaves_the_phase_it_was_in_failed_and_the_rest_passed():
    # Step 2 must answer a field it cannot (`n: int`), with no retries.
    failing = json.loads(json.dumps(FLOW))
    del failing["steps"][1]["out"]
    failing["steps"][1].update(outputs={"n": "int"}, retries=0)
    item = _Item(_HoldStepTwo(), failing)
    with item.client:
        run_id = item.launch()
        item.runner.release.set()
        assert item.settle(run_id) == "error"
        stored = item.stored(run_id)
    assert stored == {"first": "passed", "second": "failed"}


def test_an_error_no_step_raised_leaves_the_phase_it_was_in_failed():
    # The wall-clock cap ends the run while step 2 is still working: no step
    # failed, so the phase it was in is still running when the run errors.
    item = _Item(_HoldStepTwo(), workflow_run_timeout=timedelta(seconds=1))
    with item.client:
        run_id = item.launch()
        assert item.settle(run_id) == "error"
        stored = item.stored(run_id)
        item.runner.release.set()
    assert stored == {"first": "passed", "second": "failed"}


def test_a_finished_run_has_every_phase_passed():
    item = _Item(_HoldStepTwo())
    with item.client:
        run_id = item.launch()
        item.runner.release.set()
        assert item.settle(run_id) == "done"
        stored = item.stored(run_id)
    assert stored == {"first": "passed", "second": "passed"}


def _stored_before_the_fix(item: _Item, status: RunStatus, current: str) -> str:
    """A run as one written before this fix left it: every phase walked running."""
    return (
        item.spec.get_resource_manager(WorkflowRun)
        .create(
            WorkflowRun(
                item_id=item.base.rsplit("/", 1)[1],
                captured_user="u",
                workflow_id="flow",
                status=status,
                current_phase=current,
                started=1,
                phases=[
                    PhaseState(phase="first", status="running", done=1, total=1),
                    PhaseState(phase="second", status="running", total=1),
                ],
            )
        )
        .resource_id
    )


def _served(item: _Item, payload: dict) -> dict[str, str]:
    return {p["phase"]: p["status"] for p in payload["phases"]}


def test_a_run_stored_before_the_fix_is_served_settled():
    # P2: the reported execution itself, already in the database, reads right.
    item = _Item(_HoldStepTwo())
    with item.client:
        stopped = _stored_before_the_fix(item, RunStatus.CANCELLED, "second")
        one = item.client.get(f"{item.base}/runs/{stopped}").json()
        listed = {r["run_id"]: r for r in item.client.get(f"{item.base}/runs").json()}
    assert _served(item, one) == {"first": "passed", "second": "stopped"}
    assert _served(item, listed[stopped]) == {"first": "passed", "second": "stopped"}


def test_a_live_run_stored_before_the_fix_is_served_with_one_running_phase():
    item = _Item(_HoldStepTwo())
    with item.client:
        live = _stored_before_the_fix(item, RunStatus.RUNNING, "second")
        one = item.client.get(f"{item.base}/runs/{live}").json()
        listed = {r["run_id"]: r for r in item.client.get(f"{item.base}/runs").json()}
    assert _served(item, one) == {"first": "passed", "second": "running"}
    assert _served(item, listed[live]) == {"first": "passed", "second": "running"}


GATED = {
    "id": "flow",
    "title": "Draft then review",
    "phases": [{"id": "first", "title": "Draft"}, {"id": "review", "title": "Review"}],
    "steps": [
        {
            "type": "agent",
            "cache": False,
            "name": "s1",
            "phase": "first",
            "prompt": "STEP1",
            "out": "a.md",
        },
        {
            "type": "gate",
            "name": "ok",
            "phase": "review",
            "title": "Go on?",
            "allow": ["approve", "reject"],
        },
    ],
}


class _HoldSecondDraft:
    """Answers the first draft at once and holds the redraft a revise asks for."""

    def __init__(self) -> None:
        self.drafts = 0
        self.in_redraft = threading.Event()
        self.release = threading.Event()

    async def run(self, prompt: str, ctx: Any):  # noqa: ANN201
        self.drafts += 1
        if self.drafts > 1:
            self.in_redraft.set()
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        yield MessageDelta(text="draft")
        yield RunDone()


def _await_gate(item: _Item, run_id: str) -> None:
    deadline = time.monotonic() + 10
    while item.client.get(f"{item.base}/runs/{run_id}").json()["status"] != "awaiting_human":
        assert time.monotonic() < deadline, "never reached the gate"
        time.sleep(0.02)
    time.sleep(0.1)


def test_a_revise_re_enters_the_draft_and_leaves_the_review():
    # A gate's revise goes back to an earlier step: the phase left is entered again.
    revising = json.loads(json.dumps(GATED))
    revising["steps"][0]["prompt"] = "STEP1 {steps.ok.feedback}"
    revising["steps"][1].update(allow=["approve", "revise", "reject"], revise_to="s1")
    runner = _HoldSecondDraft()
    item = _Item(runner, revising)  # ty: ignore[invalid-argument-type]
    with item.client:
        put = item.client.put(
            f"{item.base}/files/.workflows/flow.json", content=json.dumps(revising)
        )
        assert put.status_code == 204
        run_id = item.client.post(f"{item.base}/run?workflow_id=flow").json()["run_id"]
        _await_gate(item, run_id)
        decided = item.client.post(
            f"{item.base}/runs/{run_id}/decisions", json={"choice": "revise", "input": "shorter"}
        )
        assert decided.status_code in (200, 202, 204), decided.text
        assert runner.in_redraft.wait(10)
        redrafting = item.stored(run_id)
        runner.release.set()
        _await_gate(item, run_id)
    assert redrafting == {"first": "running", "review": "passed"}


def test_at_a_gate_the_phases_before_it_passed_and_the_gates_is_left_as_it_is():
    item = _Item(_HoldStepTwo(), GATED)
    with item.client:
        put = item.client.put(f"{item.base}/files/.workflows/flow.json", content=json.dumps(GATED))
        assert put.status_code == 204
        run_id = item.client.post(f"{item.base}/run?workflow_id=flow").json()["run_id"]
        deadline = time.monotonic() + 10
        while item.client.get(f"{item.base}/runs/{run_id}").json()["status"] != "awaiting_human":
            assert time.monotonic() < deadline, "never reached the gate"
            time.sleep(0.02)
        time.sleep(0.1)
        stored = item.stored(run_id)
    assert stored == {"first": "passed", "review": "running"}
