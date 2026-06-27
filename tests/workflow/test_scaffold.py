"""Workflow scaffold (#287) — `new` generates a runnable, annotated run.py + the
matching _profile.json workflows entry so a developer starts from working code, not a
blank file. Every recipe's output must pass `check` (phases declared = phases emitted)."""

from __future__ import annotations

import json

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.authoring import check_profile_dir
from workspace_app.workflow.discovery import _exec_run
from workspace_app.workflow.gate import AwaitingHuman, record_decision
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.scaffold import RECIPES, scaffold_workflow


def _load(apps_dir, workflow_id="wf"):
    return _exec_run(_profile_dir(apps_dir) / "workflows" / workflow_id / "run.py", "live")


def _fake_handle(reply="a short note"):
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")

    async def drive_turn(prompt, tools):  # the agent REPLIES with content (#107)
        return reply

    wf.drive_turn = drive_turn
    return wf


def _profile_dir(apps_dir, slug="myapp", profile="default"):
    return apps_dir / slug / "profiles" / profile


def _workflow_ids(apps_dir):
    data = json.loads((_profile_dir(apps_dir) / "_profile.json").read_text())
    return [w["id"] for w in data["workflows"]]


def test_scaffold_minimal_creates_a_check_clean_workflow(tmp_path):
    paths = scaffold_workflow(tmp_path, "myapp", "default", "hello")
    pdir = _profile_dir(tmp_path)
    run_py = pdir / "workflows" / "hello" / "run.py"
    profile_json = pdir / "_profile.json"
    assert run_py in paths and profile_json in paths
    assert run_py.exists() and profile_json.exists()

    entry = json.loads(profile_json.read_text())["workflows"][0]
    assert entry["id"] == "hello"
    assert [p["id"] for p in entry["phases"]] == ["note"]
    # the whole point: the generated workflow is coherent under `check`
    assert check_profile_dir(pdir, "myapp/default") == []


@pytest.mark.parametrize("recipe", sorted(RECIPES))
def test_every_recipe_is_check_clean_and_actually_loads(tmp_path, recipe):
    """The strongest guarantee: each recipe's run.py is check-clean (phases declared =
    emitted) AND `_exec_run` executes it — so the template's imports resolve and it
    defines run(), not just that it parses."""
    scaffold_workflow(tmp_path, "myapp", "default", "wf", recipe=recipe)
    pdir = _profile_dir(tmp_path)
    assert check_profile_dir(pdir, "myapp/default") == []
    run = _exec_run(pdir / "workflows" / "wf" / "run.py", f"t_{recipe}")
    assert callable(run)


def test_scaffold_appends_to_an_existing_profile(tmp_path):
    scaffold_workflow(tmp_path, "myapp", "default", "first")
    scaffold_workflow(tmp_path, "myapp", "default", "second", recipe="batch")
    assert _workflow_ids(tmp_path) == ["first", "second"]
    assert check_profile_dir(_profile_dir(tmp_path), "myapp/default") == []


def test_scaffold_refuses_to_overwrite_without_force(tmp_path):
    scaffold_workflow(tmp_path, "myapp", "default", "dup")
    with pytest.raises(ValueError, match="already exists"):
        scaffold_workflow(tmp_path, "myapp", "default", "dup")


def test_scaffold_force_overwrites_in_place(tmp_path):
    scaffold_workflow(tmp_path, "myapp", "default", "dup", recipe="minimal")
    scaffold_workflow(tmp_path, "myapp", "default", "dup", recipe="batch", force=True)
    workflows = json.loads((_profile_dir(tmp_path) / "_profile.json").read_text())["workflows"]
    assert len(workflows) == 1  # replaced, not duplicated
    assert workflows[0]["tag"] == "batch"
    assert check_profile_dir(_profile_dir(tmp_path), "myapp/default") == []


def test_scaffold_rejects_an_unknown_recipe(tmp_path):
    with pytest.raises(ValueError, match="unknown recipe"):
        scaffold_workflow(tmp_path, "myapp", "default", "x", recipe="nope")


@pytest.mark.parametrize("bad_id", ["", "a/b"])
def test_scaffold_rejects_a_bad_id(tmp_path, bad_id):
    with pytest.raises(ValueError, match="must be non-empty"):
        scaffold_workflow(tmp_path, "myapp", "default", bad_id)


def test_scaffold_refuses_a_legacy_singular_profile(tmp_path):
    pdir = _profile_dir(tmp_path)
    pdir.mkdir(parents=True)
    (pdir / "_profile.json").write_text(json.dumps({"workflow": {"phases": [{"id": "x"}]}}))
    with pytest.raises(ValueError, match="legacy singular"):
        scaffold_workflow(tmp_path, "myapp", "default", "new")


def test_generated_run_py_carries_the_workflow_id(tmp_path):
    scaffold_workflow(tmp_path, "myapp", "default", "ingest-logs")
    text = (_profile_dir(tmp_path) / "workflows" / "ingest-logs" / "run.py").read_text()
    assert "ingest-logs —" in text  # docstring token was substituted
    assert "__WF_ID__" not in text


# ── P6: the scaffolded recipes actually run to their documented terminal state.
# Driven with a fake agent turn (no LLM) so the orchestration shape is verified
# deterministically; the recipes compose only already-live-checked blocks.


async def test_minimal_recipe_runs_to_done(tmp_path):
    scaffold_workflow(tmp_path, "myapp", "default", "wf", recipe="minimal")
    wf = _fake_handle("Hello — welcome aboard.")
    result = await _load(tmp_path)(wf, {})
    assert result == {"status": "done", "wrote": "note.md"}
    assert "Hello" in await wf.read_text("note.md")


async def test_review_commit_recipe_pauses_at_the_gate_then_commits(tmp_path):
    scaffold_workflow(tmp_path, "myapp", "default", "wf", recipe="review-commit")
    run = _load(tmp_path)
    wf = _fake_handle("# Draft\nThe proposal body.")

    # first reach: produces the draft, then suspends at the human gate
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    assert await wf.exists("draft.md")
    assert not await wf.exists("committed.md")  # nothing committed before approval

    # approve → re-run replays the produce step, finds the decision, commits
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    assert result == {"status": "approved", "committed": "committed.md"}
    assert await wf.read_text("committed.md") == await wf.read_text("draft.md")


async def test_review_commit_recipe_reject_stops_without_committing(tmp_path):
    scaffold_workflow(tmp_path, "myapp", "default", "wf", recipe="review-commit")
    run = _load(tmp_path)
    wf = _fake_handle("# Draft\nbody")
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await record_decision(wf, phase="review", choice="reject")
    assert await run(wf, {}) == {"status": "rejected"}
    assert not await wf.exists("committed.md")


async def test_batch_recipe_processes_each_upload(tmp_path):
    scaffold_workflow(tmp_path, "myapp", "default", "wf", recipe="batch")
    wf = _fake_handle("A two-sentence summary. It is short.")
    await wf.write("uploads/a.txt", b"alpha")
    await wf.write("uploads/b.txt", b"beta")
    await wf.write("uploads/input.json", b"{}")  # excluded, not processed
    result = await _load(tmp_path)(wf, {})
    assert result["status"] == "done" and result["processed"] == 2
    assert await wf.exists("notes/uploads_a.txt.md")
    assert await wf.exists("notes/uploads_b.txt.md")
