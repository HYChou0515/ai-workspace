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
    body = await read_skill_impl(RunContextWrapper(ctx), "report-format")
    assert body.startswith("# Fmt")


async def test_read_skill_refuses_a_skill_toggled_off_for_the_item(isolated_apps: Path):
    """#380: a skill the item turned OFF (``skill_prefs`` False) is not readable —
    ``read_skill`` refuses it (defense in depth), matching its absence from the
    advertised index. The refusal names the skill + points at the picker."""
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "local-lab", "report-format", "fmt", "# Fmt\n\nbody.")
    ctx = AgentToolContext(
        app_slug="rca",
        template_profile="local-lab",
        skill_prefs={"report-format": False},
    )
    out = await read_skill_impl(RunContextWrapper(ctx), "report-format")
    assert "error" in out
    assert "report-format" in out
    assert "off" in out.lower()


async def test_read_skill_allows_an_applied_skill_even_when_toggled_off(isolated_apps: Path):
    """#380: a skill APPLIED this turn is readable even if its per-item toggle is
    OFF — apply overrides the disable (its body is preloaded anyway; read_skill
    stays consistent and doesn't refuse it)."""
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "local-lab", "report-format", "fmt", "# Fmt\n\nbody.")
    ctx = AgentToolContext(
        app_slug="rca",
        template_profile="local-lab",
        skill_prefs={"report-format": False},
        applied_skills=["report-format"],
    )
    out = await read_skill_impl(RunContextWrapper(ctx), "report-format")
    assert out.startswith("# Fmt")  # applied → readable despite the off toggle


async def test_read_skill_unknown_name_returns_error_listing_available(isolated_apps: Path):
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "local-lab", "good", "ok", "body")
    ctx = AgentToolContext(app_slug="rca", template_profile="local-lab")
    out = await read_skill_impl(RunContextWrapper(ctx), "nope")
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
    a = await read_skill_impl(
        RunContextWrapper(AgentToolContext(app_slug="rca", template_profile="local-lab")), "fmt"
    )
    b = await read_skill_impl(
        RunContextWrapper(AgentToolContext(app_slug="rca", template_profile="smt")), "fmt"
    )
    assert a == "A-body"
    assert b == "B-body"


async def test_read_skill_does_not_wake_sandbox(isolated_apps: Path):
    """Skills are host-side markdown — `read_skill` must not wake the sandbox."""
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "local-lab", "fmt", "x", "body")

    class _BlowUpSandbox:
        async def create(self, spec, sandbox_id=None):
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
    body = await read_skill_impl(RunContextWrapper(ctx), "fmt")
    assert body == "body"


async def test_read_skill_without_app_or_profile_returns_error():
    """A context with no App/profile (e.g. KB chat reusing the runner) →
    friendly error, never crash."""
    from workspace_app.agent.tools import read_skill_impl

    ctx = AgentToolContext(app_slug=None, template_profile=None)
    out = await read_skill_impl(RunContextWrapper(ctx), "anything")
    assert "error" in out
    assert "App workspace" in out


# ─── workspace skills (#298) ──────────────────────────────────────────


def _ws_ctx(slug: str | None = "rca", profile: str | None = "default"):
    from workspace_app.files import WorkspaceFiles
    from workspace_app.filestore.memory import MemoryFileStore

    files = WorkspaceFiles(MemoryFileStore())
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1", files=files, app_slug=slug, template_profile=profile
        )
    )


async def _write(ctx, path: str, content: str) -> None:
    from workspace_app.agent.tools import write_file_impl

    await write_file_impl(ctx, path, content)


async def test_read_skill_reads_a_workspace_skill():
    """A skill written into the workspace `.skill/<name>/SKILL.md` loads via
    `read_skill`, independent of any package/profile skill (#298 Q1/Q3a)."""
    from workspace_app.agent.tools import read_skill_impl

    ctx = _ws_ctx()
    await _write(
        ctx,
        "/.skill/my-skill/SKILL.md",
        "---\nname: my-skill\ndescription: do X\n---\n\n# Body\n\nstep 1",
    )
    out = await read_skill_impl(ctx, "my-skill")
    assert out.startswith("# Body")


async def test_workspace_skill_shadows_package_skill_of_same_name(isolated_apps: Path):
    """When a workspace and a package skill share a name, the workspace one
    (the user's own) wins — it's read first."""
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "default", "fmt", "x", "PACKAGE-body")
    ctx = _ws_ctx()
    md = "---\nname: fmt\ndescription: d\n---\n\nWORKSPACE-body"
    await _write(ctx, "/.skill/fmt/SKILL.md", md)
    assert await read_skill_impl(ctx, "fmt") == "WORKSPACE-body"


async def test_read_skill_miss_lists_workspace_skills_too(isolated_apps: Path):
    """An unknown name lists the available skills including the workspace ones,
    so the agent can recover by picking a real one."""
    from workspace_app.agent.tools import read_skill_impl

    (isolated_apps / "rca" / "profiles" / "default").mkdir(parents=True)
    ctx = _ws_ctx()
    await _write(ctx, "/.skill/my-ws/SKILL.md", "---\nname: my-ws\ndescription: d\n---\n\nbody")
    out = await read_skill_impl(ctx, "ghost")
    assert "error:" in out
    assert "my-ws" in out


async def test_read_skill_loads_a_shared_skill(monkeypatch, tmp_path: Path):
    """A built-in (shared) skill from the registry loads via read_skill, after
    the workspace shadow check (#298 Q7)."""
    import workspace_app.apps.shared_skills as shared
    from workspace_app.agent.tools import read_skill_impl

    d = tmp_path / "author-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: author-skill\ndescription: meta\n---\n\nSHARED-body")
    monkeypatch.setattr(shared, "SHARED_SKILLS", {"author-skill": d})
    ctx = _ws_ctx(slug="rca", profile="default")
    assert await read_skill_impl(ctx, "author-skill") == "SHARED-body"


async def test_read_skill_miss_lists_workspace_skills_when_no_profile():
    """No App/profile (e.g. a context that only has a workspace), unknown name →
    the error still lists the workspace skills so the agent can recover."""
    from workspace_app.agent.tools import read_skill_impl

    ctx = _ws_ctx(slug=None, profile=None)
    await _write(ctx, "/.skill/only-ws/SKILL.md", "---\nname: only-ws\ndescription: d\n---\n\nbody")
    out = await read_skill_impl(ctx, "ghost")
    assert "error" in out
    assert "only-ws" in out


async def test_read_skill_workspace_body_over_cap_returns_error(monkeypatch):
    import workspace_app.apps.skills as skills
    from workspace_app.agent.tools import read_skill_impl

    monkeypatch.setattr(skills, "SKILL_BODY_CAP", 5)
    ctx = _ws_ctx()
    await _write(ctx, "/.skill/big/SKILL.md", "---\nname: big\ndescription: d\n---\n\n" + "x" * 20)
    out = await read_skill_impl(ctx, "big")
    assert "error" in out


async def test_read_skill_shared_body_over_cap_returns_error(monkeypatch, tmp_path: Path):
    import workspace_app.apps.shared_skills as shared
    import workspace_app.apps.skills as skills
    from workspace_app.agent.tools import read_skill_impl

    d = tmp_path / "huge-shared"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: huge-shared\ndescription: d\n---\n\n" + "x" * 20)
    monkeypatch.setattr(shared, "SHARED_SKILLS", {"huge-shared": d})
    monkeypatch.setattr(skills, "SKILL_BODY_CAP", 5)
    out = await read_skill_impl(_ws_ctx(), "huge-shared")
    assert "error" in out


def test_build_tools_tolerates_unknown_app_manifest():
    """`build_tools` for a slug with no app.json (e.g. a synthetic test slug) must
    not crash resolving its shared skills — it just exposes no read_skill."""
    from workspace_app.agent.tools import build_tools

    names = {t.name for t in build_tools(app_slug="no-such-app-xyz", profile="default")}
    assert "read_skill" not in names


def test_build_tools_wires_read_skill_via_declared_shared_skill(monkeypatch, tmp_path: Path):
    """An App with no package skills but a declared shared skill still gets
    read_skill — so the author-skill entry point is reachable."""
    import workspace_app.apps.shared_skills as shared
    from workspace_app.agent import tools as tools_mod
    from workspace_app.agent.tools import build_tools

    d = tmp_path / "author-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: author-skill\ndescription: meta\n---\n\nbody")
    monkeypatch.setattr(shared, "SHARED_SKILLS", {"author-skill": d})
    monkeypatch.setattr(tools_mod, "_declared_shared_skills", lambda slug: ["author-skill"])
    names = {t.name for t in build_tools(app_slug="playground", profile="default")}
    assert "read_skill" in names


# ─── build_tools conditional injection ────────────────────────────────


def test_build_tools_includes_read_skill_when_profile_has_skills(isolated_apps: Path):
    from workspace_app.agent.tools import build_tools

    _profile_with_skill(isolated_apps, "rca", "local-lab", "fmt", "x", "body")
    tools = build_tools(app_slug="rca", profile="local-lab")
    assert "read_skill" in {t.name for t in tools}


def test_build_tools_omits_read_skill_when_profile_has_no_skills(isolated_apps: Path, monkeypatch):
    import workspace_app.apps.shared_skills as shared
    from workspace_app.agent.tools import build_tools

    # No package skills AND no shared skills (empty registry) → no read_skill.
    monkeypatch.setattr(shared, "SHARED_SKILLS", {})
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
    assert {"exec", "read_file", "write_file", "list_files"} <= names


# ─── AgentToolContext fields exist ────────────────────────────────────


def test_agent_tool_context_app_slug_and_profile_default_none():
    ctx = AgentToolContext()
    assert ctx.app_slug is None
    assert ctx.template_profile is None
    ctx2 = AgentToolContext(app_slug="rca", template_profile="local-lab")
    assert ctx2.app_slug == "rca"
    assert ctx2.template_profile == "local-lab"
