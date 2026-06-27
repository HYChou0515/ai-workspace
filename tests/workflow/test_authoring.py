"""Authoring-time checks (#287) — a *static* coherence pass over a workflow
profile so a mistake surfaces with an actionable message before startup, not as a
boot crash. Errors: a missing / ``run()``-less / syntactically broken ``run.py``,
empty/duplicate list-form ids, empty phase ids. Warning: a ``phase="literal"`` in
``run.py`` not declared in the manifest (the drift/typo case)."""

from __future__ import annotations

import json
from pathlib import Path

from workspace_app.workflow.authoring import Diagnostic, check_app, check_profile_dir


def _write_profile(profile_dir: Path, manifest: dict, runs: dict[str, str] | None = None) -> None:
    """Lay out a profile dir: ``_profile.json`` + each list-form workflow's
    ``workflows/<id>/run.py`` (or a legacy root ``run.py`` under key ``""``)."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "_profile.json").write_text(json.dumps(manifest))
    for wf_id, body in (runs or {}).items():
        run_dir = profile_dir if wf_id == "" else profile_dir / "workflows" / wf_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.py").write_text(body)


_CLEAN_RUN = (
    "async def run(wf, inputs):\n"
    "    await agent_write_step(wf, phase='note', out='note.md', prompt='hi')\n"
    "    return {'status': 'done'}\n"
)


def _levels(diags: list[Diagnostic]) -> list[str]:
    return [d.level for d in diags]


def test_clean_workflow_profile_has_no_diagnostics(tmp_path):
    _write_profile(
        tmp_path,
        {"workflows": [{"id": "hello", "phases": [{"id": "note"}]}]},
        {"hello": _CLEAN_RUN},
    )
    assert check_profile_dir(tmp_path, "app/p") == []


def test_missing_run_py_is_an_error(tmp_path):
    # declares a workflow but never writes its run.py
    _write_profile(tmp_path, {"workflows": [{"id": "hello", "phases": [{"id": "note"}]}]})
    diags = check_profile_dir(tmp_path, "app/p")
    assert _levels(diags) == ["error"]
    assert "run.py is missing" in diags[0].message


def test_run_py_without_a_run_function_is_an_error(tmp_path):
    _write_profile(
        tmp_path,
        {"workflows": [{"id": "hello", "phases": []}]},
        {"hello": "x = 1  # no run() here\n"},
    )
    diags = check_profile_dir(tmp_path, "app/p")
    assert _levels(diags) == ["error"]
    assert "no run()" in diags[0].message


def test_run_py_with_a_syntax_error_is_an_error(tmp_path):
    _write_profile(
        tmp_path,
        {"workflows": [{"id": "hello", "phases": []}]},
        {"hello": "async def run(wf, inputs)\n    return {}\n"},  # missing colon
    )
    diags = check_profile_dir(tmp_path, "app/p")
    assert _levels(diags) == ["error"]
    assert "syntax error" in diags[0].message


def test_sync_def_run_also_counts(tmp_path):
    # a plain (non-async) def run is still a run() — no error
    _write_profile(
        tmp_path,
        {"workflows": [{"id": "hello", "phases": []}]},
        {"hello": "def run(wf, inputs):\n    return {}\n"},
    )
    assert check_profile_dir(tmp_path, "app/p") == []


def test_empty_phase_id_is_an_error(tmp_path):
    _write_profile(
        tmp_path,
        {"workflows": [{"id": "hello", "phases": [{"id": ""}]}]},
        {"hello": _CLEAN_RUN},
    )
    diags = check_profile_dir(tmp_path, "app/p")
    assert any(d.level == "error" and "phase" in d.message for d in diags)


def test_empty_workflow_id_is_an_error(tmp_path):
    _write_profile(
        tmp_path,
        {"workflows": [{"id": "", "phases": []}]},
        {"": "async def run(wf, inputs):\n    return {}\n"},
    )
    diags = check_profile_dir(tmp_path, "app/p")
    assert any(d.level == "error" and "missing its 'id'" in d.message for d in diags)


def test_duplicate_workflow_id_is_an_error(tmp_path):
    _write_profile(
        tmp_path,
        {"workflows": [{"id": "dup", "phases": []}, {"id": "dup", "phases": []}]},
        {"dup": "async def run(wf, inputs):\n    return {}\n"},
    )
    diags = check_profile_dir(tmp_path, "app/p")
    assert any(d.level == "error" and "duplicate workflow id" in d.message for d in diags)


def test_phase_literal_not_declared_is_a_warning(tmp_path):
    # run.py emits phase="typo" but the manifest only declares "note"
    body = (
        "async def run(wf, inputs):\n"
        "    await agent_write_step(wf, phase='typo', out='x.md', prompt='hi')\n"
        "    return {}\n"
    )
    _write_profile(
        tmp_path,
        {"workflows": [{"id": "hello", "phases": [{"id": "note"}]}]},
        {"hello": body},
    )
    diags = check_profile_dir(tmp_path, "app/p")
    assert _levels(diags) == ["warning"]
    assert "'typo'" in diags[0].message


def test_non_literal_phase_expression_is_not_flagged(tmp_path):
    # phase=<expr> can't be verified statically → never a false-positive warning
    body = (
        "async def run(wf, inputs):\n"
        "    p = 'whatever'\n"
        "    await agent_write_step(wf, phase=p, out='x.md', prompt='hi')\n"
        "    return {}\n"
    )
    _write_profile(tmp_path, {"workflows": [{"id": "hello", "phases": []}]}, {"hello": body})
    assert check_profile_dir(tmp_path, "app/p") == []


def test_legacy_singular_workflow_form_is_supported(tmp_path):
    _write_profile(
        tmp_path,
        {"workflow": {"phases": [{"id": "note"}]}},
        {"": _CLEAN_RUN},
    )
    assert check_profile_dir(tmp_path, "app/p") == []


def test_interactive_profile_yields_no_diagnostics(tmp_path):
    # a profile with neither `workflows` nor a `workflow` block is interactive-only
    _write_profile(tmp_path, {"title": "just chat"})
    assert check_profile_dir(tmp_path, "app/p") == []


def test_diagnostic_render_with_and_without_hint():
    assert Diagnostic("error", "a/p", "boom").render() == "error: a/p: boom"
    rendered = Diagnostic("warning", "a/p", "boom", "do x").render()
    assert rendered == "warning: a/p: boom\n    hint: do x"


def test_check_app_is_clean_for_shipped_apps():
    # the bundled workflow Apps are coherent (also the P4 gate, asserted per-app here)
    assert check_app("playground") == []
    assert check_app("topic-hub") == []


def test_check_app_handles_an_app_without_workflows():
    # rca ships only interactive profiles → nothing to check
    assert check_app("rca") == []
