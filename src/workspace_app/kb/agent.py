"""KB-flavoured AgentConfig factory. Mirrors rca.agent.default_rca_agent_config
but wires only the kb_search tool (no sandbox/file tools) and the KB prompt."""

from __future__ import annotations

from ..resources import AgentConfig
from .prompts import load_kb_system_prompt


def default_kb_agent_config() -> AgentConfig:
    """An AgentConfig for the knowledge-base agent: the KB system prompt and
    kb_search as its only tool. No sandbox image / idle timeout matter — the
    KB agent never spins one up."""
    return AgentConfig(
        name="KB Agent",
        system_prompt=load_kb_system_prompt(),
        allowed_tools=["kb_search"],
    )
