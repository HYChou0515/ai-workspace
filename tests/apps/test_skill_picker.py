"""#380 — the per-item skills picker resolver (`effective_item_skills`) and the
`GET /a/{slug}/items/{id}/skills` endpoint it backs.

Uses the real `_template` fixture app (declares two shared skills, default profile
opts one in) so the default-on / default-off + tri-state override paths are
exercised end to end against production loaders — no synthetic package.
"""

from __future__ import annotations

from workspace_app.apps.skills import SkillMeta, effective_item_skills


def _by_name(states):
    return {s.name: s for s in states}


def test_effective_item_skills_marks_a_profile_opted_in_shared_skill_default_on():
    """A shared skill the default profile opts into (`author-skill`) is source
    'shared', default_on, and effective with no per-item override."""
    states = _by_name(effective_item_skills("_template", "default", {}, []))
    s = states["author-skill"]
    assert s.source == "shared"
    assert s.default_on is True
    assert s.effective is True


def test_effective_item_skills_marks_a_declared_but_unopted_skill_default_off():
    """A shared skill the App declares but the profile leaves out of `skills`
    (`author-workflow`) is available-but-default-OFF: default_on False, effective
    False (still listed so the picker can offer to turn it on)."""
    states = _by_name(effective_item_skills("_template", "default", {}, []))
    s = states["author-workflow"]
    assert s.source == "shared"
    assert s.default_on is False
    assert s.effective is False


def test_effective_item_skills_force_on_makes_a_default_off_skill_effective():
    """A per-item `skill_prefs` True flips a default-off skill effective (its
    default_on stays False — the pref is the override, not the default)."""
    states = _by_name(
        effective_item_skills("_template", "default", {"author-workflow": True}, [])
    )
    s = states["author-workflow"]
    assert s.default_on is False
    assert s.effective is True


def test_effective_item_skills_includes_workspace_skills_as_default_on():
    """A co-created workspace skill is listed source 'workspace', default_on +
    effective — the picker surfaces it alongside the built-ins."""
    ws = [SkillMeta(name="my-skill", description="do X")]
    states = _by_name(effective_item_skills("_template", "default", {}, ws))
    s = states["my-skill"]
    assert s.source == "workspace"
    assert s.default_on is True
    assert s.effective is True
