"""§A.S2 — `workspace_app.rca.skills` exposes:

- `SkillMeta(name, description)` (frozen Struct) — what we surface
  to the agent (system-prompt index entry).
- `list_skills(profile)` walks `templates/<profile>/.skill/<name>/SKILL.md`
  and returns SkillMeta list, sorted by name. Cached.
- `load_skill(profile, name)` returns the body markdown (frontmatter
  stripped). Cached. Raises `SkillError` on unknown name or body cap.
- `SKILL_BODY_CAP` (50_000 chars) prevents a runaway-sized skill from
  blowing up the agent's tool-response budget.

Tests use a tmp template package fixture — no production state read."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated_templates(tmp_path: Path, monkeypatch):
    """Mint a synthetic templates package layout at ``tmp_path/pkg/``
    and monkeypatch ``workspace_app.rca.skills._TEMPLATES_PKG`` to point
    at it. Returns the root of the synthetic templates package.

    Layout:
        pkg/__init__.py
        pkg/foo/__init__.py
        pkg/foo/.skill/<name>/SKILL.md      ← seeded by individual tests
    """
    root = tmp_path / "skill_pkg_root"
    pkg = root / "skillpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(root))
    # Reload skills.py so its @cache wrapper picks up the new _TEMPLATES_PKG.
    import workspace_app.rca.skills as skills

    importlib.reload(skills)
    monkeypatch.setattr(skills, "_TEMPLATES_PKG", "skillpkg")
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    yield pkg
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    sys.modules.pop("skillpkg", None)


def _make_profile(pkg_root: Path, profile: str) -> Path:
    """Create a profile dir with `__init__.py` so `importlib.resources`
    can navigate to it; return the `.skill/` dir."""
    prof = pkg_root / profile
    prof.mkdir()
    (prof / "__init__.py").write_text("")
    skill_dir = prof / ".skill"
    skill_dir.mkdir()
    return skill_dir


def _make_skill(skill_dir: Path, name: str, description: str, body: str) -> None:
    """Drop a SKILL.md at ``skill_dir/<name>/SKILL.md`` with YAML
    frontmatter `name` + `description` and the given body."""
    sd = skill_dir / name
    sd.mkdir()
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
    )


# ─── SkillMeta + frontmatter parsing ─────────────────────────────────


def test_skillmeta_parsed_from_frontmatter(isolated_templates: Path):
    """frontmatter `name` + `description` → SkillMeta with those fields."""
    from workspace_app.rca.skills import SkillMeta, list_skills

    sk = _make_profile(isolated_templates, "methodology")
    _make_skill(sk, "5-why-walkthrough", "Walk through 5 Whys.", "body")
    [meta] = list_skills("methodology")
    assert isinstance(meta, SkillMeta)
    assert meta.name == "5-why-walkthrough"
    assert meta.description == "Walk through 5 Whys."


# ─── list_skills ──────────────────────────────────────────────────────


def test_list_skills_returns_alphabetical_meta_for_template_with_skills(
    isolated_templates: Path,
):
    """Multi-skill profile → SkillMeta list sorted by name."""
    from workspace_app.rca.skills import list_skills

    sk = _make_profile(isolated_templates, "methodology")
    _make_skill(sk, "fishbone-6m", "6M fishbone.", "body")
    _make_skill(sk, "5-why-walkthrough", "5 whys.", "body")
    _make_skill(sk, "stop-the-line-checklist", "When to stop.", "body")
    metas = list_skills("methodology")
    assert [m.name for m in metas] == [
        "5-why-walkthrough",
        "fishbone-6m",
        "stop-the-line-checklist",
    ]


def test_list_skills_returns_empty_for_template_without_skills(isolated_templates: Path):
    """A profile without a `.skill/` dir → empty list (skill_index is
    conditionally skipped in compose_system_prompt)."""
    from workspace_app.rca.skills import list_skills

    _make_profile(isolated_templates, "default")  # no skill dir
    assert list_skills("default") == []


def test_list_skills_returns_empty_for_unknown_profile(isolated_templates: Path):
    """Asking for skills of a non-existent profile is not an error —
    just empty. Mirrors `load_template_appendix`'s behaviour."""
    from workspace_app.rca.skills import list_skills

    assert list_skills("does-not-exist") == []


# ─── load_skill body ─────────────────────────────────────────────────


def test_load_skill_strips_frontmatter_returns_body(isolated_templates: Path):
    """body markdown is what the agent reads after `read_skill(name)` —
    frontmatter is metadata and must be stripped."""
    from workspace_app.rca.skills import load_skill

    sk = _make_profile(isolated_templates, "methodology")
    _make_skill(
        sk,
        "5-why-walkthrough",
        "Walk through 5 Whys.",
        "# 5 Whys\n\nAsk why five times.",
    )
    body = load_skill("methodology", "5-why-walkthrough")
    assert body.startswith("# 5 Whys")
    assert "Ask why five times." in body
    assert "name:" not in body
    assert "description:" not in body


def test_load_skill_unknown_name_raises_skill_error(isolated_templates: Path):
    """An unknown skill name → SkillError so the tool layer surfaces a
    clean error message (with available list)."""
    from workspace_app.rca.skills import SkillError, load_skill

    _make_profile(isolated_templates, "methodology")
    with pytest.raises(SkillError):
        load_skill("methodology", "nope")


def test_load_skill_body_exceeding_cap_raises_skill_error(isolated_templates: Path):
    """A body over SKILL_BODY_CAP → SkillError (don't truncate — methodology
    can't lose its tail; author should split the skill)."""
    from workspace_app.rca import skills as skill_mod
    from workspace_app.rca.skills import SKILL_BODY_CAP, SkillError, load_skill

    sk = _make_profile(isolated_templates, "methodology")
    big = "x" * (SKILL_BODY_CAP + 100)
    _make_skill(sk, "huge", "Huge.", big)
    # cache may have a previous run's hit, but the test fixture clears it.
    skill_mod.load_skill.cache_clear()
    with pytest.raises(SkillError, match="exceeds"):
        load_skill("methodology", "huge")


# ─── frontmatter robustness ─────────────────────────────────────────


def test_frontmatter_missing_name_skips_skill_with_warning(isolated_templates: Path, caplog):
    """A SKILL.md whose frontmatter has no `name` is skipped (logged
    warning) — corrupt skills shouldn't crash the boot."""
    import logging

    from workspace_app.rca.skills import list_skills

    sk = _make_profile(isolated_templates, "methodology")
    # Hand-craft frontmatter without `name`.
    sd = sk / "anonymous"
    sd.mkdir()
    (sd / "SKILL.md").write_text("---\ndescription: missing name field\n---\n\nbody")
    # And a valid one for the result list.
    _make_skill(sk, "good", "Good skill.", "body")

    with caplog.at_level(logging.WARNING):
        metas = list_skills("methodology")
    assert [m.name for m in metas] == ["good"]
    assert any("anonymous" in r.message for r in caplog.records)


def test_frontmatter_malformed_yaml_skips_skill_with_warning(isolated_templates: Path, caplog):
    """Broken YAML frontmatter → skip + warn. Same forgiving stance as
    a missing `name`."""
    import logging

    from workspace_app.rca.skills import list_skills

    sk = _make_profile(isolated_templates, "methodology")
    sd = sk / "broken"
    sd.mkdir()
    (sd / "SKILL.md").write_text("---\nname: broken\ndescription: [unclosed list\n---\nbody")
    _make_skill(sk, "good", "Good skill.", "body")

    with caplog.at_level(logging.WARNING):
        metas = list_skills("methodology")
    assert [m.name for m in metas] == ["good"]
    assert any("broken" in r.message for r in caplog.records)


def test_skill_dir_name_mismatch_with_frontmatter_name_skips_with_warning(
    isolated_templates: Path, caplog
):
    """The directory name MUST match `frontmatter.name`. Author typo
    (mismatched) → skip with a clear warning."""
    import logging

    from workspace_app.rca.skills import list_skills

    sk = _make_profile(isolated_templates, "methodology")
    sd = sk / "5-why-walkthrough"
    sd.mkdir()
    # Mismatch: dir is `5-why-walkthrough` but frontmatter says `wrong`.
    (sd / "SKILL.md").write_text("---\nname: wrong\ndescription: x\n---\nbody")
    with caplog.at_level(logging.WARNING):
        metas = list_skills("methodology")
    assert metas == []
    assert any("mismatch" in r.message.lower() for r in caplog.records)


def test_skill_directory_without_skill_md_is_skipped(isolated_templates: Path):
    """An empty subdir under `.skill/` (no SKILL.md) is silently
    skipped — author may be mid-edit."""
    from workspace_app.rca.skills import list_skills

    sk = _make_profile(isolated_templates, "methodology")
    (sk / "in-progress").mkdir()  # no SKILL.md
    _make_skill(sk, "done", "Done.", "body")
    assert [m.name for m in list_skills("methodology")] == ["done"]


def test_list_skills_ignores_non_dir_entries_under_skill_dir(isolated_templates: Path):
    """A stray file under `.skill/` (not a skill subdir) is silently
    skipped — `list_skills` only considers directories."""
    from workspace_app.rca.skills import list_skills

    sk = _make_profile(isolated_templates, "methodology")
    (sk / "README.md").write_text("not a skill")  # file, not a skill subdir
    _make_skill(sk, "good", "ok", "body")
    assert [m.name for m in list_skills("methodology")] == ["good"]


def test_load_skill_for_profile_without_skills_raises(isolated_templates: Path):
    """`load_skill` against a profile with no `.skill/` dir → SkillError
    that explicitly says so."""
    from workspace_app.rca.skills import SkillError, load_skill

    (isolated_templates / "default").mkdir()
    (isolated_templates / "default" / "__init__.py").write_text("")
    with pytest.raises(SkillError, match="has no skills"):
        load_skill("default", "anything")


def test_parse_frontmatter_returns_empty_when_no_frontmatter_block():
    """SKILL.md without `---` frontmatter → ({}, body) so list_skills
    treats it as a missing-name skill (skipped with warning)."""
    from workspace_app.rca.skills import _parse_frontmatter

    front, body = _parse_frontmatter(b"# Just a body\n\nno frontmatter here.")
    assert front == {}
    assert body.startswith("# Just a body")


def test_parse_frontmatter_returns_empty_when_no_closing_fence():
    """An open `---` without a matching closing fence is treated as no
    frontmatter (rather than guessing where it ends)."""
    from workspace_app.rca.skills import _parse_frontmatter

    raw = b"---\nname: maybe\n# never closes\n# more body\n"
    front, body = _parse_frontmatter(raw)
    assert front == {}
    assert body.startswith("---")


def test_parse_frontmatter_rejects_non_mapping_yaml():
    """Frontmatter that parses but isn't a mapping (e.g. a line w/o `:`)
    raises SkillError so list_skills logs + skips."""
    from workspace_app.rca.skills import SkillError, _parse_frontmatter

    raw = b"---\nname-no-colon\n---\nbody"
    with pytest.raises(SkillError, match="malformed"):
        _parse_frontmatter(raw)


def test_parse_yaml_tolerates_blank_lines_and_comments():
    """`_parse_yaml` skips blank lines and `#` comments — author may
    indent their frontmatter cleanly without breaking the parse."""
    from workspace_app.rca.skills import _parse_yaml

    out = _parse_yaml("name: foo\n\n# a comment\ndescription: bar\n")
    assert out == {"name": "foo", "description": "bar"}


def test_balanced_helper_detects_unmatched_close():
    """`_balanced` flags `description: ]foo` (close before open) as
    unbalanced — guards against subtle frontmatter bugs."""
    from workspace_app.rca.skills import _balanced

    assert _balanced("[ok]") is True
    assert _balanced("[") is False  # depth remains > 0
    assert _balanced("]") is False  # close before open
