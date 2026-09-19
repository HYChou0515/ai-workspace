"""#298 P3 — shared (built-in) skills, introduced like tool-packages: a
`SHARED_SKILLS` registry (name → source dir) that an App opts into via
`app.json` `agent.skills`. `author-skill` (the co-authoring meta-skill) is one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import workspace_app.apps.shared_skills as shared
from workspace_app.apps import skills as skills_mod


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
    assert shared.shared_skill_metas(["demo", "ghost"]) == [skills_mod.SkillMeta("demo", "a demo")]


def test_metas_empty_for_no_names(tmp_registry):
    assert shared.shared_skill_metas([]) == []


def test_load_returns_body(tmp_registry):
    assert shared.load_shared_skill("demo").startswith("# Demo")


def test_load_unknown_raises(tmp_registry):
    with pytest.raises(skills_mod.SkillError):
        shared.load_shared_skill("ghost")


def test_load_body_over_cap_raises(tmp_registry, monkeypatch):
    monkeypatch.setattr(skills_mod, "SKILL_BODY_CAP", 10)
    tmp_registry("huge", "d", "x" * 11)
    monkeypatch.setitem(shared.SHARED_SKILLS, "huge", shared.SHARED_SKILLS["demo"].parent / "huge")
    with pytest.raises(skills_mod.SkillError, match="exceeds"):
        shared.load_shared_skill("huge")


def test_metas_skips_registered_name_with_no_skill_md(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(shared, "SHARED_SKILLS", {"empty": empty})
    assert shared.shared_skill_metas(["empty"]) == []


def test_metas_skips_frontmatter_name_mismatch(tmp_path, monkeypatch):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: other\ndescription: d\n---\n\nbody")
    monkeypatch.setattr(shared, "SHARED_SKILLS", {"demo": d})
    assert shared.shared_skill_metas(["demo"]) == []


def test_metas_skips_malformed_frontmatter(tmp_path, monkeypatch):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "SKILL.md").write_text("---\ndescription: [unbalanced\n---\n\nbody")
    monkeypatch.setattr(shared, "SHARED_SKILLS", {"demo": d})
    assert shared.shared_skill_metas(["demo"]) == []


def test_author_skill_is_registered():
    """The co-authoring meta-skill ships in the real registry."""
    assert "author-skill" in shared.SHARED_SKILLS


def test_author_skill_ships_the_writing_rules_and_points_at_them():
    """The author asked that a skill be TIDIED before it is saved — and chose to
    do it the way Matt Pocock's own repo does: the rules live in the guide the
    drafting agent reads, not in a mechanism behind `save_skill`. So the rules
    (his `writing-for-agents`, MIT) ship as a reference file of `author-skill`,
    and the body reaches it at the step where the draft is written — a rule the
    body never points at is one the agent never reads, and `materialize_skill`
    only copies what the folder actually holds."""
    folder = shared.SHARED_SKILLS["author-skill"]
    rules = folder / "references" / "writing-for-agents.md"
    assert rules.is_file()
    body = (folder / "SKILL.md").read_text()
    # The pointer sits in the drafting step, worded as a step (read it, apply
    # it), so it is on the path every drafting run takes.
    draft, _, _ = body.partition("## 4.")
    assert "references/writing-for-agents.md" in draft
    # …and the tidy-before-save pass is its own step: the checks the reviewer
    # will run at publish time, applied by the author first.
    assert "no-op" in body.lower()
    assert "single source of truth" in body.lower()
    # The reference carries the levers the body names, so the pointer resolves
    # to the thing it promises.
    text = rules.read_text()
    for lever in ("Leading word", "Negation", "Pruning", "completion criterion"):
        assert lever in text, lever
    # Provenance: copied text says where it came from.
    assert "mattpocock/skills" in text and "MIT" in text


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


def test_augment_author_workflow_appends_grammar_and_boundaries():
    """plan §3.2: the purpose-only author-workflow skill body is augmented at load with the
    machine-derived DSL grammar (P5) + this app's capability/tool boundaries (P6)."""
    from workspace_app.apps.skills import augment_shared_skill_body

    out = augment_shared_skill_body("author-workflow", "PURPOSE-ONLY", None, None)
    assert "PURPOSE-ONLY" in out
    assert "machine-derived reference" in out  # the grammar (P5)
    assert "can and cannot do" in out  # the boundaries (P6)


def test_augment_leaves_other_shared_skills_unchanged():
    from workspace_app.apps.skills import augment_shared_skill_body

    assert augment_shared_skill_body("author-skill", "BODY", "playground", "dsl") == "BODY"
