"""#298 P3 — shared (built-in) skills, introduced like tool-packages: a
`SHARED_SKILLS` registry (name → source dir) that an App opts into via
`app.json` `agent.skills`. `author-skill` (the co-authoring meta-skill) is one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import workspace_app.apps.shared_skills as shared


@pytest.fixture
def tmp_registry(tmp_path: Path, monkeypatch):
    """Point SHARED_SKILLS at a synthetic registry so tests don't depend on the
    real `sample-skills/` content."""

    def make(name: str, description: str, body: str) -> Path:
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}")
        return d

    registry = {"demo": make("demo", "a demo", "# Demo\n\nbody")}
    monkeypatch.setattr(shared, "SHARED_SKILLS", registry)
    return make


def test_metas_reads_registered_skills(tmp_registry):
    metas = shared.shared_skill_metas(["demo"])
    assert [(m.name, m.description) for m in metas] == [("demo", "a demo")]


def test_metas_skips_unregistered_name(tmp_registry):
    """A name not in the registry is dropped (the manifest coherence check is the
    loud guard; the loader itself is lenient)."""
    assert shared.shared_skill_metas(["demo", "ghost"]) == [shared.SkillMeta("demo", "a demo")]


def test_metas_empty_for_no_names(tmp_registry):
    assert shared.shared_skill_metas([]) == []


def test_load_returns_body(tmp_registry):
    assert shared.load_shared_skill("demo").startswith("# Demo")


def test_load_unknown_raises(tmp_registry):
    with pytest.raises(shared.SkillError):
        shared.load_shared_skill("ghost")


def test_author_skill_is_registered():
    """The co-authoring meta-skill ships in the real registry."""
    assert "author-skill" in shared.SHARED_SKILLS


def test_merged_profile_skills_includes_declared_shared(tmp_registry):
    """resolve()'s index source merges declared shared skills with the profile's
    own package skills (#298 Q7)."""
    from workspace_app.apps.skills import merged_profile_skills

    # No package profile skills for this synthetic slug → just the shared one.
    metas = merged_profile_skills("no-such-app", "default", ["demo"])
    assert [m.name for m in metas] == ["demo"]


def test_validate_function_coherence_rejects_unknown_shared_skill(tmp_registry):
    """A typo'd `agent.skills` name fails the boot loud."""
    from workspace_app.apps.catalog import validate_function_coherence
    from workspace_app.apps.manifest import AgentManifest, AppManifest, ItemNouns

    manifest = AppManifest(
        slug="x",
        title="X",
        agent=AgentManifest(prompt_file="p.md", skills=["ghost"]),
        item=ItemNouns(noun="case", noun_plural="cases"),
    )
    with pytest.raises(ValueError, match="ghost"):
        validate_function_coherence(manifest)
