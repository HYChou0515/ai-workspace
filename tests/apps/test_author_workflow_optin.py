"""#323 / #400 — the workspace apps opt into the author-workflow meta-skill +
save_workflow, the workflow analogue of #298's author-skill / save_skill. #400
widens the opt-in from Topic Hub alone to every real App (matching author-skill's
``OPTED_IN``); the ``_template`` scaffold carries the grant too so new Apps inherit
it (it isn't a served App, so it's asserted statically, not parametrized)."""

import pytest

from workspace_app.agent import build_tools
from workspace_app.apps.manifest import load_app_manifest
from workspace_app.apps.shared_skills import SHARED_SKILLS, load_shared_skill
from workspace_app.apps.skills import merged_profile_skills

OPTED_IN = ["rca", "topic-hub", "playground"]


def test_author_workflow_is_registered_in_the_shared_skill_registry():
    assert "author-workflow" in SHARED_SKILLS
    assert "Co-author a workflow" in load_shared_skill("author-workflow")


@pytest.mark.parametrize("slug", OPTED_IN)
def test_app_opts_into_author_workflow_and_save_workflow(slug: str):
    manifest = load_app_manifest(slug)
    assert "author-workflow" in manifest.agent.skills
    assert "save_workflow" in manifest.agent.tools


@pytest.mark.parametrize("slug", OPTED_IN)
def test_app_advertises_author_workflow_in_its_skill_index(slug: str):
    manifest = load_app_manifest(slug)
    metas = merged_profile_skills(slug, manifest.default_profile, manifest.agent.skills)
    assert "author-workflow" in {m.name for m in metas}


@pytest.mark.parametrize("slug", OPTED_IN)
def test_app_exposes_save_workflow_tool(slug: str):
    manifest = load_app_manifest(slug)
    names = {
        t.name
        for t in build_tools(manifest.agent.tools, app_slug=slug, profile=manifest.default_profile)
    }
    assert "save_workflow" in names


def test_template_scaffold_carries_the_grant_so_new_apps_inherit_it():
    """``_template`` is the copy-me scaffold (not a served App, so not in
    ``OPTED_IN``); it must ship the grant so a scaffolded App can co-author
    workflows out of the box (#400)."""
    manifest = load_app_manifest("_template")
    assert "author-workflow" in manifest.agent.skills
    assert "save_workflow" in manifest.agent.tools
