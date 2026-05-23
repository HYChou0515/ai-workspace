"""RCA-flavored AgentConfig factory. Used by the entrypoint and by
tests that need an agent talking RCA out of the box."""

from __future__ import annotations

from ..resources import AgentConfig
from .prompts import load_system_prompt


def default_rca_agent_config() -> AgentConfig:
    """An AgentConfig pre-loaded with the RCA system prompt + the
    default RCA-tuned sandbox image + 8h idle timeout."""
    return AgentConfig(name="RCA Agent", system_prompt=load_system_prompt())
