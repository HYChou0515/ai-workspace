"""`skill-hub` — the shared skill that teaches when to search, install or
publish a skill (plan-skill-hub P11).

Opts in the same way as its siblings: a folder under ``sample-skills/``, an
entry in ``SHARED_SKILLS``, and the name in each granting App's ``app.json``
``agent.skills``. Review round 1 found the guard missing: renaming the
frontmatter made the loader skip the skill in silence — the exact trap the
hub's own validator exists to catch — and 99 tests stayed green.
"""

import pytest

from workspace_app.apps.manifest import load_app_manifest
from workspace_app.apps.profiles import load_profile
from workspace_app.apps.shared_skills import SHARED_SKILLS, load_shared_skill, shared_skill_metas
from workspace_app.apps.skills import effective_item_skills

SKILL = "skill-hub"
TOOLS = ("publish_skill", "install_skill", "search_skill_hub")
GRANTING_APPS = ("rca", "pm", "playground", "topic-hub")


def test_skill_hub_is_registered_in_the_shared_skill_registry():
    assert SKILL in SHARED_SKILLS
    assert "search_skill_hub" in load_shared_skill(SKILL)


def test_frontmatter_name_matches_the_folder_so_it_is_not_silently_skipped():
    metas = shared_skill_metas([SKILL])
    assert [m.name for m in metas] == [SKILL]
    assert metas[0].description.strip()


@pytest.mark.parametrize("slug", GRANTING_APPS)
def test_every_app_that_grants_the_three_tools_declares_the_skill(slug: str):
    """The tools without the guidance is a model that publishes on "save";
    the guidance without the tools is a skill that describes tools the model
    does not hold. They travel together."""
    manifest = load_app_manifest(slug)
    assert set(TOOLS) <= set(manifest.agent.tools)
    assert SKILL in manifest.agent.skills


@pytest.mark.parametrize("slug", GRANTING_APPS)
def test_the_skill_is_on_by_default_in_each_granting_apps_default_profile(slug: str):
    """A profile that narrows `skills` (pm/default did, and left this one out)
    turns the guidance off for every item on that profile while the tools stay
    granted — the regression lens's finding."""
    manifest = load_app_manifest(slug)
    profile = manifest.default_profile
    states = {s.name: s for s in effective_item_skills(slug, profile, {}, [], tools=None)}
    assert states[SKILL].default_on is True, (slug, profile, load_profile(slug, profile).skills)
