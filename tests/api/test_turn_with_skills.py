"""§A.S5 / #89 end-to-end — when an item's App profile ships skills, the
agent's tool list (built by the LitellmAgentRunner via `_agent_for`) includes
`read_skill`. When the profile has none (or no App), it doesn't.

Skills live at `apps/<slug>/profiles/<profile>/.skill/`; we mint a synthetic
apps package so production profiles are untouched.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


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


def _profile_with_skill(root: Path, slug: str, profile: str, name: str = "demo") -> None:
    sd = root / slug / "profiles" / profile / ".skill" / name
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A demo skill.\n---\n\nbody")


def _profile_without_skill(root: Path, slug: str, profile: str) -> None:
    (root / slug / "profiles" / profile).mkdir(parents=True)


def test_agent_for_with_profile_having_skills_exposes_read_skill(isolated_apps: Path):
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    _profile_with_skill(isolated_apps, "rca", "local-lab")
    agent = _agent_for(AgentConfig(name="a"), app_slug="rca", template_profile="local-lab")
    assert "read_skill" in {t.name for t in agent.tools}


def test_agent_for_with_profile_without_skills_omits_read_skill(isolated_apps: Path, monkeypatch):
    import workspace_app.apps.shared_skills as shared
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    # No package skills AND no shared skills (empty registry) → no read_skill.
    monkeypatch.setattr(shared, "SHARED_SKILLS", {})
    _profile_without_skill(isolated_apps, "rca", "default")
    agent = _agent_for(AgentConfig(name="a"), app_slug="rca", template_profile="default")
    assert "read_skill" not in {t.name for t in agent.tools}


def test_agent_for_without_app_or_profile_omits_read_skill():
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    agent = _agent_for(AgentConfig(name="a"))
    assert "read_skill" not in {t.name for t in agent.tools}


async def test_turn_agent_exposes_read_skill_pointing_at_the_shared_impl(isolated_apps: Path):
    """Join-point: the runner-built agent surfaces read_skill with a callable
    schema, and the registry points at the same impl the unit tests exercise."""
    from workspace_app.agent.tools import _IMPLS, build_tools, read_skill_impl
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    _profile_with_skill(isolated_apps, "rca", "local-lab")
    agent = _agent_for(AgentConfig(name="a"), app_slug="rca", template_profile="local-lab")
    read_skill_tool = next(t for t in agent.tools if t.name == "read_skill")
    assert "name" in read_skill_tool.params_json_schema["properties"]  # ty: ignore
    assert "read_skill" in {t.name for t in build_tools(app_slug="rca", profile="local-lab")}
    assert _IMPLS["read_skill"] is read_skill_impl
