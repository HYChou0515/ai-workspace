"""Profile workflow discovery (#100) — loading a profile's run() by file path
(so hyphenated profile dirs work) + the startup coherence validation."""

import pytest

from workspace_app.workflow.discovery import (
    WorkflowNotFound,
    _check_phase_ids,
    _exec_run,
    load_run_callable,
    validate_workflow_profiles,
)
from workspace_app.workflow.manifest import WorkflowManifest, WorkflowPhase


def test_load_run_callable_returns_the_run():
    run = load_run_callable("playground", "echo")
    assert callable(run) and run.__name__ == "run"  # ty: ignore[unresolved-attribute]


def test_load_run_callable_missing_run_py_raises():
    # playground/default is an ordinary interactive profile — no run.py.
    with pytest.raises(WorkflowNotFound):
        load_run_callable("playground", "default")


def test_validate_workflow_profiles_passes_for_shipped_app():
    # The shipped playground workflow profiles (echo, intake) are coherent.
    validate_workflow_profiles("playground")


def test_validate_skips_apps_without_workflows():
    # rca ships only interactive profiles → nothing to validate, no error.
    validate_workflow_profiles("rca")


def test_exec_run_rejects_a_run_py_without_a_run_callable(tmp_path):
    bad = tmp_path / "run.py"
    bad.write_text("x = 1  # no run() here\n")
    with pytest.raises(WorkflowNotFound, match="no run"):
        _exec_run(bad, "label")


def test_exec_run_loads_a_valid_run(tmp_path):
    good = tmp_path / "run.py"
    good.write_text("async def run(wf, inputs):\n    return inputs\n")
    fn = _exec_run(good, "label")
    assert callable(fn) and fn.__name__ == "run"  # ty: ignore[unresolved-attribute]


def test_check_phase_ids_rejects_a_phase_missing_its_id():
    bad = WorkflowManifest(phases=[WorkflowPhase(id="ok"), WorkflowPhase(id="")])
    with pytest.raises(ValueError, match="missing its 'id'"):
        _check_phase_ids(bad, "app/profile")
