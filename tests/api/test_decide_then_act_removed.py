"""The decide-then-act experiment is DELETED (prod incident, 2026-07-23).

Someone switched WORKSPACE_AGENT_DECIDE_THEN_ACT on in a deployment and every
chat surface started repeating itself — the wrapper drove turns without the
model's own prior replies, so the agent looped like a broken record while the
llm logs showed requests missing the assistant history. The knob must be DEAD:
setting the env var changes nothing, and no module ships the wrapper."""

import importlib

import pytest

from workspace_app.api.litellm_runner import _agent_for
from workspace_app.resources import AgentConfig


def test_the_env_var_no_longer_changes_the_model(monkeypatch):
    monkeypatch.setenv("WORKSPACE_AGENT_DECIDE_THEN_ACT", "1")
    agent = _agent_for(AgentConfig(name="ws"))
    assert type(agent.model).__name__ != "DecideThenActModel"


def test_the_wrapper_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("workspace_app.agent.decide_then_act")
