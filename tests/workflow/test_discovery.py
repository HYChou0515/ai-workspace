"""Profile workflow discovery (#100) — loading a profile's run() by file path
(so hyphenated profile dirs work) + the startup coherence validation."""

import pytest

from workspace_app.workflow.discovery import (
    WorkflowNotFound,
    _check_phase_ids,
    _check_workflow_ids,
    _exec_run,
    load_preflight_callable,
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


# ── Phase 5: multiple workflows per profile (manual §4) ──────────────────


def test_load_run_callable_loads_a_workflow_subdir_run():
    """A list-form workflow's run.py lives at profiles/<name>/workflows/<id>/run.py
    and is loaded by file path (the existing exec mechanism)."""
    run = load_run_callable("playground", "multi", "alpha")
    assert callable(run) and run.__name__ == "run"  # ty: ignore[unresolved-attribute]
    run_beta = load_run_callable("playground", "multi", "beta")
    assert callable(run_beta) and run_beta.__name__ == "run"  # ty: ignore[unresolved-attribute]


def test_load_run_callable_missing_workflow_subdir_raises():
    with pytest.raises(WorkflowNotFound):
        load_run_callable("playground", "multi", "nope")


def test_load_run_callable_legacy_root_run_still_loads():
    """With no workflow_id (the legacy singular form), run.py is read from the
    profile root — back-compat preserved."""
    run = load_run_callable("playground", "echo")
    assert callable(run) and run.__name__ == "run"  # ty: ignore[unresolved-attribute]


def test_validate_workflow_profiles_validates_every_workflow_of_a_list_profile():
    """The multi-workflow fixture (playground/multi) passes startup validation: both
    workflows' run.py load + every phase carries an id."""
    validate_workflow_profiles("playground")  # echo + intake (legacy) + multi (list)


def test_check_workflow_ids_rejects_a_workflow_missing_its_id():
    bad = [WorkflowManifest(id="ok"), WorkflowManifest(id="")]
    with pytest.raises(ValueError, match="missing its 'id'"):
        _check_workflow_ids(bad, "app/profile")


def test_check_workflow_ids_rejects_duplicate_ids():
    bad = [WorkflowManifest(id="dup"), WorkflowManifest(id="dup")]
    with pytest.raises(ValueError, match="duplicate workflow id"):
        _check_workflow_ids(bad, "app/profile")


# ── #283: optional pre-flight hook ───────────────────────────────────────


def test_load_preflight_callable_returns_the_hook_when_present():
    """echo/run.py declares ``preflight`` alongside ``run`` — discovery loads it."""
    pf = load_preflight_callable("playground", "echo")
    assert callable(pf) and pf.__name__ == "preflight"  # ty: ignore[unresolved-attribute]


def test_load_preflight_callable_returns_none_when_absent():
    """Pre-flight is optional: a run.py without one yields None (the dialog then just
    shows the workflow's phases)."""
    assert load_preflight_callable("playground", "multi", "beta") is None


def test_load_preflight_callable_missing_run_py_is_none():
    """No run.py at all ⇒ no pre-flight (defensive — never raises)."""
    assert load_preflight_callable("playground", "multi", "nope") is None
