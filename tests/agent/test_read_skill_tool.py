"""§A.S4 — `read_skill` tool + `AgentToolContext.template_profile` +
`build_tools(profile=)` conditional injection.

Tests use the same isolated-templates synthetic package fixture as
`tests/rca/test_skills.py` so production templates are untouched.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext


@pytest.fixture
def isolated_templates(tmp_path: Path, monkeypatch):
    root = tmp_path / "tpl_root"
    pkg = root / "tplpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(root))
    import workspace_app.rca.skills as skills

    importlib.reload(skills)
    monkeypatch.setattr(skills, "_TEMPLATES_PKG", "tplpkg")
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    yield pkg
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    sys.modules.pop("tplpkg", None)


def _profile_with_skill(root: Path, profile: str, name: str, description: str, body: str) -> None:
    prof = root / profile
    prof.mkdir()
    (prof / "__init__.py").write_text("")
    skill_dir = prof / ".skill"
    skill_dir.mkdir()
    sd = skill_dir / name
    sd.mkdir()
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}")


# ─── read_skill tool body ─────────────────────────────────────────────


async def test_read_skill_returns_body_for_known_skill(isolated_templates: Path):
    """Tool returns the skill body when the name is registered. Uses
    the impl directly (faster than spinning up an Agent for unit work)."""
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(
        isolated_templates,
        "methodology",
        "5-why",
        "Walk through 5 Whys.",
        "# 5 Whys\n\nAsk why five times.",
    )
    ctx = AgentToolContext(template_profile="methodology")
    body = await read_skill_impl(RunContextWrapper(ctx), "5-why")  # ty: ignore[invalid-argument-type]
    assert body.startswith("# 5 Whys")


async def test_read_skill_unknown_name_returns_friendly_error_listing_available(
    isolated_templates: Path,
):
    """Unknown skill → friendly error string listing what IS available,
    so the agent recovers without another query roundtrip (§A.Q5)."""
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_templates, "methodology", "good", "ok", "body")
    ctx = AgentToolContext(template_profile="methodology")
    out = await read_skill_impl(RunContextWrapper(ctx), "nope")  # ty: ignore[invalid-argument-type]
    assert "error:" in out
    assert "nope" in out
    assert "available" in out.lower()
    assert "good" in out


async def test_read_skill_without_template_profile_returns_error():
    """A context with no template_profile (e.g. KB chat reusing the
    runner) → friendly error, never crash."""
    from workspace_app.agent.tools import read_skill_impl

    ctx = AgentToolContext(template_profile=None)
    out = await read_skill_impl(RunContextWrapper(ctx), "anything")  # ty: ignore[invalid-argument-type]
    assert "error" in out
    assert "RCA workspace" in out


# ─── build_tools conditional injection ────────────────────────────────


def test_build_tools_includes_read_skill_when_profile_has_skills(
    isolated_templates: Path,
):
    """`build_tools(profile="methodology")` exposes read_skill when the
    profile ships any skill (§A.Q6 "same flag in/out")."""
    from workspace_app.agent.tools import build_tools

    _profile_with_skill(isolated_templates, "methodology", "5-why", "x", "body")
    tools = build_tools(profile="methodology")
    names = {t.name for t in tools}
    assert "read_skill" in names


def test_build_tools_omits_read_skill_when_profile_has_no_skills(
    isolated_templates: Path,
):
    """A profile without `.skill/` content → no read_skill tool. Avoids
    a dead tool slot in the LLM's tool list."""
    from workspace_app.agent.tools import build_tools

    # Create the profile but no skill dir.
    (isolated_templates / "default").mkdir()
    (isolated_templates / "default" / "__init__.py").write_text("")
    tools = build_tools(profile="default")
    names = {t.name for t in tools}
    assert "read_skill" not in names


def test_build_tools_omits_read_skill_when_profile_is_none():
    """No profile in scope → no read_skill (KB flavour, tests, …)."""
    from workspace_app.agent.tools import build_tools

    tools = build_tools(profile=None)
    names = {t.name for t in tools}
    assert "read_skill" not in names


def test_build_tools_keeps_default_workspace_tools(isolated_templates: Path):
    """Adding read_skill doesn't drop the existing workspace toolset."""
    from workspace_app.agent.tools import build_tools

    _profile_with_skill(isolated_templates, "methodology", "5-why", "x", "body")
    tools = build_tools(profile="methodology")
    names = {t.name for t in tools}
    assert {"exec", "read_file", "write_file", "ls"} <= names


# ─── AgentToolContext field exists ────────────────────────────────────


def test_agent_tool_context_template_profile_default_none():
    """The field exists with a None default; doesn't break legacy
    callers that don't set it."""
    ctx = AgentToolContext()
    assert ctx.template_profile is None
    ctx2 = AgentToolContext(template_profile="methodology")
    assert ctx2.template_profile == "methodology"
