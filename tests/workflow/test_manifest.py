"""Profile-level workflow discovery (manual §3, §14) — a profile carries a workflow
by declaring a `workflow` block in `_profile.json`; its presence makes the profile
headless-triggerable."""

import msgspec

from workspace_app.apps.profiles import (
    ProfileManifest,
    load_workflow_manifest,
    profile_workflows,
    workflow_profiles,
)
from workspace_app.workflow.manifest import WorkflowManifest


def test_profile_json_with_workflow_block_decodes():
    """A `_profile.json` declaring a workflow parses into a WorkflowManifest with its
    phase skeleton + input.json location."""
    raw = b"""
    {
      "title": "Intake",
      "workflow": {
        "title": "Classify & file uploads",
        "phases": [{"id": "classify", "title": "Classify"}, {"id": "ingest"}],
        "input_json": "inputs/input.json"
      }
    }
    """
    pm = msgspec.json.decode(raw, type=ProfileManifest)
    assert isinstance(pm.workflow, WorkflowManifest)
    assert [p.id for p in pm.workflow.phases] == ["classify", "ingest"]
    assert pm.workflow.phases[1].title == ""  # title optional
    assert pm.workflow.input_json == "inputs/input.json"


def test_profile_json_without_workflow_is_interactive():
    """An ordinary profile (no `workflow` block) decodes with workflow=None — it is
    interactive-only, not headless-triggerable."""
    pm = msgspec.json.decode(b'{"title": "Default"}', type=ProfileManifest)
    assert pm.workflow is None


def test_workflow_manifest_defaults_input_json_location():
    """input_json defaults to the conventional inputs/input.json when omitted."""
    wf = msgspec.json.decode(b"{}", type=WorkflowManifest)
    assert wf.input_json == "inputs/input.json"
    assert wf.phases == []


def test_existing_interactive_profiles_have_no_workflow():
    """RCA's shipped profiles are interactive (no workflow), so discovery reports
    none — confirming the helper against real package profiles."""
    assert load_workflow_manifest("rca", "default") is None
    assert workflow_profiles("rca") == []


# ── Phase 5: multiple workflows per profile (manual §4) ──────────────────


def test_workflow_manifest_carries_a_stable_id():
    """A workflow in the new list form is addressed by a stable `id` (manual §4)."""
    wf = msgspec.json.decode(b'{"id": "memory", "title": "to-memory"}', type=WorkflowManifest)
    assert wf.id == "memory"
    assert msgspec.json.decode(b"{}", type=WorkflowManifest).id == ""  # optional, "" default


def test_profile_json_with_workflows_list_decodes():
    """A `_profile.json` may declare several workflows under `workflows: [...]`; each
    carries its own id + phase skeleton (manual §4)."""
    raw = b"""
    {
      "title": "Multi",
      "workflows": [
        {"id": "memory", "title": "->memory", "phases": [{"id": "digest"}]},
        {"id": "collections", "title": "->collections", "phases": [{"id": "classify"}]}
      ]
    }
    """
    pm = msgspec.json.decode(raw, type=ProfileManifest)
    assert pm.workflow is None  # the singular block is unused in the list form
    assert [w.id for w in pm.workflows] == ["memory", "collections"]
    assert pm.workflows[0].phases[0].id == "digest"


def test_profile_workflows_lists_the_new_style_set():
    """`profile_workflows` returns every declared workflow of a list-form profile,
    against the real shipped `playground/multi` fixture."""
    wfs = profile_workflows("playground", "multi")
    assert [w.id for w in wfs] == ["alpha", "beta"]


def test_profile_workflows_normalizes_a_legacy_singular_profile():
    """A legacy `workflow:` block is normalized to a one-element list (back-compat);
    its id stays "" — the sentinel for the profile-root run.py layout."""
    wfs = profile_workflows("playground", "echo")
    assert len(wfs) == 1
    assert wfs[0].id == ""
    assert wfs[0].title == "Echo"


def test_profile_workflows_empty_for_interactive_profile():
    """An interactive profile (no workflow at all) yields no workflows."""
    assert profile_workflows("rca", "default") == []
