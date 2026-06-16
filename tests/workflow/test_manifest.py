"""Profile-level workflow discovery (manual §3, §14) — a profile carries a workflow
by declaring a `workflow` block in `_profile.json`; its presence makes the profile
headless-triggerable."""

import msgspec

from workspace_app.apps.profiles import (
    ProfileManifest,
    load_workflow_manifest,
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
