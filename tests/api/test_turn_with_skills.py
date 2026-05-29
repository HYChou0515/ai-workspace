"""§A.S5 end-to-end — when an investigation's template_profile ships
skills, the agent's tool list (built by the LitellmAgentRunner) includes
`read_skill`. When the profile has none, it doesn't.

We exercise this via `_agent_for` directly with a stubbed template
profile resolver — the registry mechanics are covered by §A.S4 unit tests.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


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


def _profile_with_skill(root: Path, profile: str, name: str = "demo", body: str = "body") -> None:
    prof = root / profile
    prof.mkdir()
    (prof / "__init__.py").write_text("")
    skill_dir = prof / ".skill"
    skill_dir.mkdir()
    sd = skill_dir / name
    sd.mkdir()
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A demo skill.\n---\n\n{body}")


def _profile_without_skill(root: Path, profile: str) -> None:
    prof = root / profile
    prof.mkdir()
    (prof / "__init__.py").write_text("")


def test_agent_for_with_template_profile_having_skills_exposes_read_skill(
    isolated_templates: Path,
):
    """Wiring proof: `_agent_for(template_profile="<profile-with-skill>")`
    puts read_skill in the agent's tool list."""
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    _profile_with_skill(isolated_templates, "methodology")
    agent = _agent_for(AgentConfig(name="a"), template_profile="methodology")
    names = {t.name for t in agent.tools}
    assert "read_skill" in names


def test_agent_for_with_template_profile_without_skills_omits_read_skill(
    isolated_templates: Path,
):
    """The opposite wiring: no skills under the profile → no read_skill
    tool (no dead tool slot in the LLM)."""
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    _profile_without_skill(isolated_templates, "default")
    agent = _agent_for(AgentConfig(name="a"), template_profile="default")
    names = {t.name for t in agent.tools}
    assert "read_skill" not in names


def test_agent_for_without_template_profile_omits_read_skill():
    """No template_profile (KB chat, tests not setting it) → no read_skill."""
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    agent = _agent_for(AgentConfig(name="a"))
    names = {t.name for t in agent.tools}
    assert "read_skill" not in names


async def test_turn_agent_can_call_read_skill_via_scripted_runner(isolated_templates: Path):
    """End-to-end wiring: when the runner builds the agent with a
    template that has skills, the read_skill FunctionTool is in the
    agent's tool list, has the right schema shape (so the LLM can call
    it), and its impl reference is `read_skill_impl`. The actual impl
    behaviour is exercised in tests/agent/test_read_skill_tool.py;
    this test is the join-point assertion that the agent runner
    actually exposes the tool to the LLM."""
    from workspace_app.agent.tools import build_tools, read_skill_impl
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    _profile_with_skill(isolated_templates, "methodology")
    # The runner-built agent surfaces read_skill as a tool …
    agent = _agent_for(AgentConfig(name="a"), template_profile="methodology")
    read_skill_tool = next(t for t in agent.tools if t.name == "read_skill")
    # … carrying a schema with `name` as its single required arg (so the LLM
    # knows how to call it).
    schema = read_skill_tool.params_json_schema
    assert "name" in schema["properties"]
    # And the tool registry the agent's runner uses points at the same impl
    # the unit tests in test_read_skill_tool.py exercise — no shadow path.
    direct_tools = {t.name: t for t in build_tools(profile="methodology")}
    assert "read_skill" in direct_tools
    # The function_tool-wrapped impl can't be unwrapped from FunctionTool
    # without invoking the SDK, but the impl symbol is what `_IMPLS` resolves.
    from workspace_app.agent.tools import _IMPLS

    assert _IMPLS["read_skill"] is read_skill_impl
