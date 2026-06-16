"""§A.S2 / #89 — `workspace_app.apps.skills`:

- `SkillMeta(name, description)` — the system-prompt index entry.
- `list_skills(app_slug, profile)` walks
  `apps/<slug>/profiles/<profile>/.skill/<name>/SKILL.md` → sorted SkillMeta list.
- `load_skill(app_slug, profile, name)` → body markdown (frontmatter stripped),
  raising `SkillError` on unknown name or `SKILL_BODY_CAP` exceeded.

Tests mint a synthetic apps package — no production state read.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated_apps(tmp_path: Path, monkeypatch):
    """Synthetic apps package at ``tmp_path/skillpkg`` + monkeypatch
    ``apps.skills._APPS_PKG`` at it. Layout:
        skillpkg/__init__.py
        skillpkg/<slug>/profiles/<profile>/.skill/<name>/SKILL.md
    """
    root = tmp_path / "skill_pkg_root"
    pkg = root / "skillpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(root))
    import workspace_app.apps.skills as skills

    importlib.reload(skills)
    monkeypatch.setattr(skills, "_APPS_PKG", "skillpkg")
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    yield pkg
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    sys.modules.pop("skillpkg", None)


def _make_profile(pkg_root: Path, slug: str, profile: str) -> Path:
    """Create `<slug>/profiles/<profile>/.skill/` and return the `.skill/` dir."""
    skill_dir = pkg_root / slug / "profiles" / profile / ".skill"
    skill_dir.mkdir(parents=True)
    return skill_dir


def _make_skill(skill_dir: Path, name: str, description: str, body: str) -> None:
    sd = skill_dir / name
    sd.mkdir()
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}")


# ─── SkillMeta + frontmatter parsing ─────────────────────────────────


def test_skillmeta_parsed_from_frontmatter(isolated_apps: Path):
    from workspace_app.apps.skills import SkillMeta, list_skills

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    _make_skill(sk, "report-format", "How to structure the report.", "body")
    [meta] = list_skills("rca", "local-lab")
    assert isinstance(meta, SkillMeta)
    assert meta.name == "report-format"
    assert meta.description == "How to structure the report."


# ─── list_skills ──────────────────────────────────────────────────────


def test_list_skills_returns_alphabetical_meta(isolated_apps: Path):
    from workspace_app.apps.skills import list_skills

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    _make_skill(sk, "zeta", "z.", "body")
    _make_skill(sk, "alpha", "a.", "body")
    _make_skill(sk, "mid", "m.", "body")
    assert [m.name for m in list_skills("rca", "local-lab")] == ["alpha", "mid", "zeta"]


def test_list_skills_returns_empty_for_profile_without_skills(isolated_apps: Path):
    (isolated_apps / "rca" / "profiles" / "default").mkdir(parents=True)  # no .skill
    from workspace_app.apps.skills import list_skills

    assert list_skills("rca", "default") == []


def test_list_skills_returns_empty_for_unknown_profile(isolated_apps: Path):
    from workspace_app.apps.skills import list_skills

    assert list_skills("rca", "does-not-exist") == []


def test_list_skills_skips_non_dir_and_missing_skill_md(isolated_apps: Path):
    """A stray file in `.skill/` and a skill dir without `SKILL.md` are both
    skipped (not skills) — only proper `<name>/SKILL.md` count."""
    from workspace_app.apps.skills import list_skills

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    (sk / "stray.txt").write_text("not a skill dir")  # non-dir entry → skipped
    (sk / "emptydir").mkdir()  # dir without SKILL.md → skipped
    _make_skill(sk, "good", "ok", "body")
    assert [m.name for m in list_skills("rca", "local-lab")] == ["good"]


def test_list_skills_skips_name_dir_mismatch(isolated_apps: Path, caplog):
    """A SKILL.md whose frontmatter `name` doesn't match its dir name is skipped
    (logged) — the dir name is the callable identity."""
    from workspace_app.apps.skills import list_skills

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    sd = sk / "dirname"
    sd.mkdir()
    (sd / "SKILL.md").write_text("---\nname: different\ndescription: x\n---\n\nbody")
    _make_skill(sk, "good", "ok", "body")
    with caplog.at_level(logging.WARNING):
        metas = list_skills("rca", "local-lab")
    assert [m.name for m in metas] == ["good"]
    assert any("mismatches" in r.message for r in caplog.records)


def test_list_skills_drops_body_only_skill_md(isolated_apps: Path):
    """A SKILL.md with no frontmatter (or an unclosed fence) parses to empty
    front → missing `name` → dropped."""
    from workspace_app.apps.skills import list_skills

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    (sk / "nofront").mkdir()
    (sk / "nofront" / "SKILL.md").write_text("# just a body, no frontmatter")
    (sk / "unclosed").mkdir()
    (sk / "unclosed" / "SKILL.md").write_text("---\nname: unclosed\nno closing fence here")
    assert list_skills("rca", "local-lab") == []


def test_load_skill_on_profile_without_skill_dir_raises(isolated_apps: Path):
    """`load_skill` on a profile that ships no `.skill/` → SkillError, not a
    crash (the runner only exposes read_skill when skills exist, but guard it)."""
    from workspace_app.apps.skills import SkillError, load_skill

    (isolated_apps / "rca" / "profiles" / "default").mkdir(parents=True)  # no .skill
    with pytest.raises(SkillError, match="no skills"):
        load_skill("rca", "default", "anything")


# ─── load_skill body ─────────────────────────────────────────────────


def test_load_skill_strips_frontmatter_returns_body(isolated_apps: Path):
    from workspace_app.apps.skills import load_skill

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    _make_skill(sk, "report-format", "Fmt.", "# Report\n\nHow to write it.")
    body = load_skill("rca", "local-lab", "report-format")
    assert body.startswith("# Report")
    assert "How to write it." in body
    assert "name:" not in body
    assert "description:" not in body


def test_load_skill_unknown_name_raises_skill_error(isolated_apps: Path):
    from workspace_app.apps.skills import SkillError, load_skill

    _make_profile(isolated_apps, "rca", "local-lab")
    with pytest.raises(SkillError):
        load_skill("rca", "local-lab", "nope")


def test_load_skill_body_exceeding_cap_raises_skill_error(isolated_apps: Path):
    from workspace_app.apps import skills as skill_mod
    from workspace_app.apps.skills import SKILL_BODY_CAP, SkillError, load_skill

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    _make_skill(sk, "huge", "Huge.", "x" * (SKILL_BODY_CAP + 100))
    skill_mod.load_skill.cache_clear()
    with pytest.raises(SkillError, match="exceeds"):
        load_skill("rca", "local-lab", "huge")


# ─── frontmatter robustness ─────────────────────────────────────────


def test_frontmatter_missing_name_skips_skill_with_warning(isolated_apps: Path, caplog):
    from workspace_app.apps.skills import list_skills

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    sd = sk / "anonymous"
    sd.mkdir()
    (sd / "SKILL.md").write_text("---\ndescription: missing name field\n---\n\nbody")
    _make_skill(sk, "good", "Good skill.", "body")
    with caplog.at_level(logging.WARNING):
        metas = list_skills("rca", "local-lab")
    assert [m.name for m in metas] == ["good"]
    assert any("anonymous" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "bad_front",
    [
        "name: broken\ndescription: [unclosed list",  # unbalanced open
        "name: broken\ndescription: []]double close",  # depth goes < 0
        "name: broken\nthis-line-has-no-colon",  # not key:value
    ],
)
def test_frontmatter_malformed_yaml_skips_skill_with_warning(
    isolated_apps: Path, caplog, bad_front
):
    from workspace_app.apps.skills import list_skills

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    (sk / "broken").mkdir()
    (sk / "broken" / "SKILL.md").write_text(f"---\n{bad_front}\n---\nbody")
    _make_skill(sk, "good", "Good skill.", "body")
    with caplog.at_level(logging.WARNING):
        metas = list_skills("rca", "local-lab")
    assert [m.name for m in metas] == ["good"]
    assert any("broken" in r.message for r in caplog.records)


def test_frontmatter_tolerates_blank_lines(isolated_apps: Path):
    """A blank line inside the frontmatter YAML is skipped, not an error."""
    from workspace_app.apps.skills import list_skills

    sk = _make_profile(isolated_apps, "rca", "local-lab")
    (sk / "spaced").mkdir()
    (sk / "spaced" / "SKILL.md").write_text("---\nname: spaced\n\ndescription: ok\n---\n\nbody")
    assert [m.name for m in list_skills("rca", "local-lab")] == ["spaced"]
