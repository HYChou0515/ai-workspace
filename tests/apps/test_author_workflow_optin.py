"""#323 — Topic Hub opts into the author-workflow meta-skill + save_workflow, the
workflow analogue of #298's author-skill / save_skill."""

import pytest

from workspace_app.agent import build_tools
from workspace_app.apps.manifest import load_app_manifest
from workspace_app.apps.shared_skills import SHARED_SKILLS, load_shared_skill
from workspace_app.apps.skills import merged_profile_skills

OPTED_IN = ["topic-hub"]


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
