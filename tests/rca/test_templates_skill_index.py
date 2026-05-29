"""§A.S3 — `compose_system_prompt` appends an "Available skills" section
when the template profile has any skills.

Layered on top of `tests/rca/test_skills.py`'s isolated_templates fixture
(synthetic templates package; production state untouched)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated_templates(tmp_path: Path, monkeypatch):
    """Synthetic templates package shared with `test_skills.py` —
    duplicated here so the file is self-contained."""
    root = tmp_path / "tpl_root"
    pkg = root / "tplpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(root))
    import workspace_app.rca.skills as skills
    import workspace_app.rca.templates as templates

    importlib.reload(skills)
    importlib.reload(templates)
    monkeypatch.setattr(skills, "_TEMPLATES_PKG", "tplpkg")
    monkeypatch.setattr(templates, "_TEMPLATES_PKG", "tplpkg")
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    yield pkg
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    sys.modules.pop("tplpkg", None)


def _profile(root: Path, name: str, prompt: str | None = None) -> Path:
    """Make a profile dir; optionally seed `_prompt.md`. Returns `.skill/`."""
    prof = root / name
    prof.mkdir()
    (prof / "__init__.py").write_text("")
    if prompt is not None:
        (prof / "_prompt.md").write_text(prompt)
    skill_dir = prof / ".skill"
    skill_dir.mkdir()
    return skill_dir


def _seed_skill(skill_dir: Path, name: str, description: str, body: str = "body") -> None:
    sd = skill_dir / name
    sd.mkdir()
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
    )


def test_compose_appends_skill_index_when_template_has_skills(isolated_templates: Path):
    """A profile with `.skill/` content → composed prompt ends with an
    "Available skills" section listing every skill."""
    from workspace_app.rca.templates import compose_system_prompt

    sk = _profile(isolated_templates, "methodology", "## Your workspace — methodology\n\nuse it.")
    _seed_skill(sk, "5-why-walkthrough", "Walk through 5 Whys.")
    _seed_skill(sk, "fishbone-6m", "6M fishbone categories.")
    out = compose_system_prompt("BASE PROMPT", "methodology")
    assert "BASE PROMPT" in out
    assert "## Your workspace — methodology" in out
    assert "## Available skills" in out
    assert "Call `read_skill(name)` to load the body" in out
    assert "- `5-why-walkthrough`: Walk through 5 Whys." in out
    assert "- `fishbone-6m`: 6M fishbone categories." in out


def test_compose_omits_skill_section_when_template_has_no_skills(isolated_templates: Path):
    """A profile with `.skill/` empty (no entries) → no Available skills
    section in the composed prompt."""
    from workspace_app.rca.templates import compose_system_prompt

    _profile(isolated_templates, "default", "## Your workspace — default")
    out = compose_system_prompt("BASE", "default")
    assert "BASE" in out
    assert "## Your workspace — default" in out
    assert "Available skills" not in out


def test_skill_index_is_sorted_alphabetical(isolated_templates: Path):
    """Skill list comes from `list_skills` (already sorted), but lock
    the rendering order so subtle reorderings don't slip through."""
    from workspace_app.rca.templates import compose_system_prompt

    sk = _profile(isolated_templates, "methodology")
    _seed_skill(sk, "zeta", "Last one.")
    _seed_skill(sk, "alpha", "First one.")
    out = compose_system_prompt("BASE", "methodology")
    alpha_idx = out.index("- `alpha`")
    zeta_idx = out.index("- `zeta`")
    assert alpha_idx < zeta_idx


def test_compose_without_appendix_or_skills_returns_bare_base(isolated_templates: Path):
    """No `_prompt.md`, no skills → composed prompt is the base only.
    Locks in the no-decoration path so a typo can't smuggle in spurious
    sections."""
    from workspace_app.rca.templates import compose_system_prompt

    _profile(isolated_templates, "barren")
    assert compose_system_prompt("BASE", "barren") == "BASE"
