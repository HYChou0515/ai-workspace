"""The orchestration driver (manual §13) — the WorkflowRun status lifecycle around
a profile's run()."""

import asyncio

import pytest
from specstar import SpecStar

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.driver import run_workflow
from workspace_app.workflow.engine import fail
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.run import RunStatus, WorkflowRun


def _run(spec: SpecStar) -> str:
    rm = spec.get_resource_manager(WorkflowRun)
    return rm.create(WorkflowRun(item_id="rca/a/1", captured_user="alice")).resource_id


def _wf() -> WorkflowHandle:
    return WorkflowHandle(store=MemoryFileStore(), workspace_id="ws")


def _clock():
    t = [1000]

    def now() -> int:
        t[0] += 1
        return t[0]

    return now


async def test_successful_run_records_done_with_result(spec_instance: SpecStar):
    rid = _run(spec_instance)

    async def run(wf, inputs):
        return {"processed": inputs["n"]}

    await run_workflow(
        spec_instance, run_id=rid, profile_run=run, wf=_wf(), inputs={"n": 3}, now=_clock()
    )
    got = spec_instance.get_resource_manager(WorkflowRun).get(rid).data
    assert got.status is RunStatus.DONE
    assert got.result == {"processed": 3}
    assert got.started is not None and got.ended is not None and got.ended > got.started


async def test_run_is_marked_running_before_it_executes(spec_instance: SpecStar):
    rid = _run(spec_instance)
    seen: list[RunStatus] = []

    async def run(wf, inputs):
        seen.append(spec_instance.get_resource_manager(WorkflowRun).get(rid).data.status)
        return {}

    await run_workflow(spec_instance, run_id=rid, profile_run=run, wf=_wf(), inputs={})
    assert seen == [RunStatus.RUNNING]


async def test_failing_run_records_error_with_message(spec_instance: SpecStar):
    rid = _run(spec_instance)

    async def run(wf, inputs):
        fail("input is malformed")

    await run_workflow(spec_instance, run_id=rid, profile_run=run, wf=_wf(), inputs={})
    got = spec_instance.get_resource_manager(WorkflowRun).get(rid).data
    assert got.status is RunStatus.ERROR
    assert "malformed" in got.result["error"]  # ty: ignore[not-subscriptable]
    assert got.ended is not None
    # docs/plan-friendly-errors.md P5: a step's reason was written for a reader —
    # it is the headline, not something to hide under "unexpected error".
    assert got.result["error_kind"] == "step_failed"  # ty: ignore[not-subscriptable]
    assert got.result["error_reason"] == "input is malformed"  # ty: ignore[not-subscriptable]


async def test_a_failed_run_keeps_the_whole_cause_chain(spec_instance: SpecStar):
    """Review round 1 (D6): the outer link alone is often the least useful one —
    an exhausted chain reads `AllProvidersFailed: …` while the 429 that WAS the
    failure sits one link down."""
    rid = _run(spec_instance)

    async def run(wf, inputs):
        try:
            raise TimeoutError("the model did not answer")
        except TimeoutError as e:
            raise RuntimeError("the step gave up") from e

    await run_workflow(spec_instance, run_id=rid, profile_run=run, wf=_wf(), inputs={})
    got = spec_instance.get_resource_manager(WorkflowRun).get(rid).data
    assert "RuntimeError: the step gave up" in got.result["error"]  # ty: ignore[not-subscriptable]
    assert "TimeoutError: the model did not answer" in got.result["error"]  # ty: ignore[not-subscriptable]


async def test_a_run_that_died_on_a_provider_is_named_by_its_kind(spec_instance: SpecStar):
    """Anything else is named like a turn's failure, the raw text kept as the
    error for technical details."""
    rid = _run(spec_instance)

    async def run(wf, inputs):
        raise TimeoutError("the model did not answer")

    await run_workflow(spec_instance, run_id=rid, profile_run=run, wf=_wf(), inputs={})
    got = spec_instance.get_resource_manager(WorkflowRun).get(rid).data
    assert got.result == {
        "error": "TimeoutError: the model did not answer",
        "error_kind": "timeout",
    }


async def test_cancelled_run_records_cancelled_and_propagates(spec_instance: SpecStar):
    rid = _run(spec_instance)

    async def run(wf, inputs):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_workflow(spec_instance, run_id=rid, profile_run=run, wf=_wf(), inputs={})
    got = spec_instance.get_resource_manager(WorkflowRun).get(rid).data
    assert got.status is RunStatus.CANCELLED


async def test_non_dict_result_is_wrapped(spec_instance: SpecStar):
    rid = _run(spec_instance)

    async def run(wf, inputs):
        return "a plain string"

    await run_workflow(spec_instance, run_id=rid, profile_run=run, wf=_wf(), inputs={})
    got = spec_instance.get_resource_manager(WorkflowRun).get(rid).data
    assert got.result == {"result": "a plain string"}
