"""`verify-number` — the shared skill that makes a computed number checkable.

RCA is the app that computes numbers off tables (SPC, Pareto, yield), and until
this skill nothing in any prompt said anything about computational correctness.
It opts in the same way as author-skill / author-workflow: a folder under
``sample-skills/``, an entry in ``SHARED_SKILLS``, and the name listed in
``app.json`` ``agent.skills``.

The last two tests pin the switch itself. That is the whole point of shipping
this as a skill rather than as prompt text: it has to be turn-off-able per item,
so the same workspace can answer the same question with and without it.
"""

import pytest

from workspace_app.apps.manifest import load_app_manifest
from workspace_app.apps.shared_skills import SHARED_SKILLS, load_shared_skill, shared_skill_metas
from workspace_app.apps.skills import effective_item_skills, merged_profile_skills

SKILL = "verify-number"


def test_verify_number_is_registered_in_the_shared_skill_registry():
    assert SKILL in SHARED_SKILLS
    assert "Write ONE script that computes and checks itself" in load_shared_skill(SKILL)


def test_frontmatter_name_matches_the_folder_so_it_is_not_silently_skipped():
    """A name/folder mismatch is dropped with only a log warning
    (``skills.py`` ``_meta``), so the skill would vanish from every index with
    nothing failing. Assert the meta actually resolves."""
    metas = shared_skill_metas([SKILL])
    assert [m.name for m in metas] == [SKILL]
    assert metas[0].description.strip()


def test_rca_opts_into_verify_number():
    assert SKILL in load_app_manifest("rca").agent.skills


def test_rca_advertises_verify_number_in_its_skill_index():
    manifest = load_app_manifest("rca")
    metas = merged_profile_skills("rca", manifest.default_profile, manifest.agent.skills)
    assert SKILL in {m.name for m in metas}


@pytest.mark.parametrize("profile", ["default", "local-lab", "tool-demo", "smt-reflow-example"])
def test_verify_number_is_on_by_default_in_every_rca_profile(profile: str):
    """None of the RCA profiles declares ``skills``, so all declared shared
    skills are on by default — which is what makes the A/B comparison a toggle
    rather than a deployment."""
    states = {s.name: s for s in effective_item_skills("rca", profile, {}, [])}
    assert states[SKILL].default_on is True
    assert states[SKILL].effective is True


def test_an_item_can_turn_verify_number_off():
    manifest = load_app_manifest("rca")
    states = {
        s.name: s
        for s in effective_item_skills("rca", manifest.default_profile, {SKILL: False}, [])
    }
    assert states[SKILL].effective is False
