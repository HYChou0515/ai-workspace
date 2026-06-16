import msgspec
from specstar import QB, SpecStar

from workspace_app.workflow.run import Failure, PhaseState, RunStatus, WorkflowRun


def test_workflow_run_round_trips_with_defaults(spec_instance: SpecStar):
    """A WorkflowRun persists + reloads, defaulting to `pending` with no phase,
    no result, capturing the owning item + the acting user."""
    rm = spec_instance.get_resource_manager(WorkflowRun)
    rev = rm.create(WorkflowRun(item_id="rca/alice/x", captured_user="alice"))
    got = rm.get(rev.resource_id).data
    assert got.item_id == "rca/alice/x"
    assert got.captured_user == "alice"
    assert got.status is RunStatus.PENDING
    assert got.current_phase == ""
    assert got.result is None


def test_runs_are_listable_by_item(spec_instance: SpecStar):
    """`item_id` is indexed, so an item's runs are a query (not a full scan), and
    one item can host several sequential runs (manual §14)."""
    rm = spec_instance.get_resource_manager(WorkflowRun)
    rm.create(WorkflowRun(item_id="rca/a/1", captured_user="a"))
    rm.create(WorkflowRun(item_id="rca/a/1", captured_user="a"))
    rm.create(WorkflowRun(item_id="rca/a/2", captured_user="a"))

    runs = list(rm.list_resources((QB["item_id"] == "rca/a/1").build()))
    assert len(runs) == 2
    assert all(r.data.item_id == "rca/a/1" for r in runs)


def test_active_runs_are_listable_by_status(spec_instance: SpecStar):
    """`status` is indexed, so "active runs" (the concurrency cap, §16) is a query."""
    rm = spec_instance.get_resource_manager(WorkflowRun)
    rm.create(WorkflowRun(item_id="i/1", captured_user="a", status=RunStatus.RUNNING))
    rm.create(WorkflowRun(item_id="i/2", captured_user="a", status=RunStatus.DONE))
    rm.create(WorkflowRun(item_id="i/3", captured_user="a", status=RunStatus.RUNNING))

    running = list(rm.list_resources((QB["status"] == RunStatus.RUNNING).build()))
    assert {r.data.item_id for r in running} == {"i/1", "i/3"}


def test_run_status_transitions_to_terminal_with_result(spec_instance: SpecStar):
    """A run advances pending → running → done, recording timing, phase progress
    and the run()'s result summary on terminal."""
    rm = spec_instance.get_resource_manager(WorkflowRun)
    rid = rm.create(WorkflowRun(item_id="i/x", captured_user="a")).resource_id

    run = rm.get(rid).data
    rm.update(
        rid,
        msgspec.structs.replace(
            run, status=RunStatus.RUNNING, started=1000, current_phase="classify"
        ),
    )
    assert rm.get(rid).data.status is RunStatus.RUNNING

    run = rm.get(rid).data
    rm.update(
        rid,
        msgspec.structs.replace(
            run,
            status=RunStatus.DONE,
            ended=2000,
            phases=[PhaseState(phase="classify", status="passed", done=2, total=2)],
            result={"processed": 2},
        ),
    )
    done = rm.get(rid).data
    assert done.status is RunStatus.DONE
    assert done.started == 1000
    assert done.ended == 2000
    assert done.result == {"processed": 2}
    assert done.phases[0].phase == "classify"
    assert done.phases[0].done == 2


def test_run_collects_per_element_failures(spec_instance: SpecStar):
    """The skip+collect loop policy (§11) records per-element failures the API can
    report on a `done` (partial) or `error` run."""
    rm = spec_instance.get_resource_manager(WorkflowRun)
    rid = rm.create(WorkflowRun(item_id="i/x", captured_user="a")).resource_id
    run = rm.get(rid).data
    rm.update(
        rid,
        msgspec.structs.replace(
            run,
            status=RunStatus.DONE,
            failures=[Failure(key="file_7.pdf", error="disallowed collection", phase="classify")],
            result={"processed": 1, "failures": 1},
        ),
    )
    got = rm.get(rid).data
    assert got.failures[0].key == "file_7.pdf"
    assert got.failures[0].phase == "classify"
