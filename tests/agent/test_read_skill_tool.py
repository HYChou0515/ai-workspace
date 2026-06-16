"""§A.S4 / #89 — `read_skill` tool + `AgentToolContext.{app_slug,template_profile}`
+ `build_tools(app_slug=, profile=)` conditional injection.

Tests use an isolated synthetic apps package so production profiles are
untouched. Skills now live at `apps/<slug>/profiles/<profile>/.skill/`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext


@pytest.fixture
def isolated_apps(tmp_path: Path, monkeypatch):
    root = tmp_path / "apps_root"
    pkg = root / "tplpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(root))
    import workspace_app.apps.skills as skills

    importlib.reload(skills)
    monkeypatch.setattr(skills, "_APPS_PKG", "tplpkg")
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    yield pkg
    skills.list_skills.cache_clear()
    skills.load_skill.cache_clear()
    sys.modules.pop("tplpkg", None)


def _profile_with_skill(
    root: Path, slug: str, profile: str, name: str, description: str, body: str
) -> None:
    sd = root / slug / "profiles" / profile / ".skill" / name
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}")


# ─── read_skill tool body ─────────────────────────────────────────────


async def test_read_skill_returns_body_for_known_skill(isolated_apps: Path):
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "local-lab", "report-format", "fmt", "# Fmt\n\nbody.")
    ctx = AgentToolContext(app_slug="rca", template_profile="local-lab")
    body = await read_skill_impl(RunContextWrapper(ctx), "report-format")  # ty: ignore[invalid-argument-type]
    assert body.startswith("# Fmt")


async def test_read_skill_unknown_name_returns_error_listing_available(isolated_apps: Path):
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "local-lab", "good", "ok", "body")
    ctx = AgentToolContext(app_slug="rca", template_profile="local-lab")
    out = await read_skill_impl(RunContextWrapper(ctx), "nope")  # ty: ignore[invalid-argument-type]
    assert "error:" in out
    assert "nope" in out
    assert "available" in out.lower()
    assert "good" in out


async def test_read_skill_uses_app_slug_and_profile_from_context(isolated_apps: Path):
    """The tool reads `ctx.app_slug` + `ctx.template_profile` to pick which
    profile's `.skill/` to scan. Same skill name in two profiles, different
    bodies; ctx switching routes the read to the right one."""
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "local-lab", "fmt", "x", "A-body")
    _profile_with_skill(isolated_apps, "rca", "smt", "fmt", "x", "B-body")
    a = await read_skill_impl(  # ty: ignore[invalid-argument-type]
        RunContextWrapper(AgentToolContext(app_slug="rca", template_profile="local-lab")), "fmt"
    )
    b = await read_skill_impl(  # ty: ignore[invalid-argument-type]
        RunContextWrapper(AgentToolContext(app_slug="rca", template_profile="smt")), "fmt"
    )
    assert a == "A-body"
    assert b == "B-body"


async def test_read_skill_does_not_wake_sandbox(isolated_apps: Path):
    """Skills are host-side markdown — `read_skill` must not wake the sandbox."""
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "local-lab", "fmt", "x", "body")

    class _BlowUpSandbox:
        async def create(self, spec):
            raise AssertionError("read_skill must NOT wake the sandbox")

        async def exec(self, *a, **kw):
            raise AssertionError("read_skill must NOT exec in the sandbox")

        async def upload(self, *a, **kw):  # pragma: no cover
            raise AssertionError("read_skill must NOT upload")

        async def kill(self, *a, **kw):  # pragma: no cover
            return None

    ctx = AgentToolContext(
        sandbox=_BlowUpSandbox(),  # ty: ignore[invalid-argument-type]
        app_slug="rca",
        template_profile="local-lab",
    )
    body = await read_skill_impl(RunContextWrapper(ctx), "fmt")  # ty: ignore[invalid-argument-type]
    assert body == "body"


async def test_read_skill_without_app_or_profile_returns_error():
    """A context with no App/profile (e.g. KB chat reusing the runner) →
    friendly error, never crash."""
    from workspace_app.agent.tools import read_skill_impl

    ctx = AgentToolContext(app_slug=None, template_profile=None)
    out = await read_skill_impl(RunContextWrapper(ctx), "anything")  # ty: ignore[invalid-argument-type]
    assert "error" in out
    assert "App workspace" in out


# ─── build_tools conditional injection ────────────────────────────────


def test_build_tools_includes_read_skill_when_profile_has_skills(isolated_apps: Path):
    from workspace_app.agent.tools import build_tools

    _profile_with_skill(isolated_apps, "rca", "local-lab", "fmt", "x", "body")
    tools = build_tools(app_slug="rca", profile="local-lab")
    assert "read_skill" in {t.name for t in tools}


def test_build_tools_omits_read_skill_when_profile_has_no_skills(isolated_apps: Path):
    from workspace_app.agent.tools import build_tools

    (isolated_apps / "rca" / "profiles" / "default").mkdir(parents=True)
    tools = build_tools(app_slug="rca", profile="default")
    assert "read_skill" not in {t.name for t in tools}


def test_build_tools_omits_read_skill_when_profile_or_app_is_none():
    from workspace_app.agent.tools import build_tools

    assert "read_skill" not in {t.name for t in build_tools(profile=None)}
    assert "read_skill" not in {t.name for t in build_tools(app_slug="rca", profile=None)}


def test_build_tools_keeps_default_workspace_tools(isolated_apps: Path):
    from workspace_app.agent.tools import build_tools

    _profile_with_skill(isolated_apps, "rca", "local-lab", "fmt", "x", "body")
    names = {t.name for t in build_tools(app_slug="rca", profile="local-lab")}
    assert {"exec", "read_file", "write_file", "ls"} <= names


# ─── AgentToolContext fields exist ────────────────────────────────────


def test_agent_tool_context_app_slug_and_profile_default_none():
    ctx = AgentToolContext()
    assert ctx.app_slug is None
    assert ctx.template_profile is None
    ctx2 = AgentToolContext(app_slug="rca", template_profile="local-lab")
    assert ctx2.app_slug == "rca"
    assert ctx2.template_profile == "local-lab"
