"""Agent-path failover wiring (#196): a preset's fallback chain makes the agent
turn run on a busy-aware FallbackModel; single-endpoint configs are untouched."""

from __future__ import annotations

from agents.models.interface import Model

from workspace_app.api.litellm_runner import LitellmAgentRunner, _agent_for
from workspace_app.config.loader import load
from workspace_app.factories import LlmEndpoint, get_runner
from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.failover.model import FallbackModel
from workspace_app.resources import AgentConfig

FallbackChains = dict[tuple[str, str | None], list[LlmEndpoint]]


def _ep(model: str, base_url: str | None) -> LlmEndpoint:
    return LlmEndpoint(
        model=model,
        base_url=base_url,
        api_key=None,
        reasoning_effort=None,
        ttft_s=8.0,
        idle_s=120.0,
        cooldown_s=30.0,
    )


def test_agent_for_wraps_in_fallback_model_when_chain_present():
    cfg = AgentConfig(name="a", model="m1", llm_base_url="http://a")
    chains: FallbackChains = {("m1", "http://a"): [_ep("m1", "http://a"), _ep("m2", "http://b")]}
    reg = CooldownRegistry(clock=lambda: 0.0)
    agent = _agent_for(cfg, fallback_chains=chains, cooldown_registry=reg)
    assert isinstance(agent.model, FallbackModel)
    # Materialising an endpoint builds a real (network-free) inner SDK model.
    inner = agent.model._make_model(_ep("m2", "http://b"))
    assert isinstance(inner, Model)


def test_agent_for_is_single_model_without_a_matching_chain():
    cfg = AgentConfig(name="a", model="m1")
    agent = _agent_for(cfg)  # no chains map at all
    assert not isinstance(agent.model, FallbackModel)


def test_agent_for_single_model_when_chain_has_one_entry():
    cfg = AgentConfig(name="a", model="m1", llm_base_url="http://a")
    chains: FallbackChains = {("m1", "http://a"): [_ep("m1", "http://a")]}  # len 1 ⇒ no failover
    reg = CooldownRegistry(clock=lambda: 0.0)
    agent = _agent_for(cfg, fallback_chains=chains, cooldown_registry=reg)
    assert not isinstance(agent.model, FallbackModel)


def test_get_runner_builds_chains_from_presets_with_fallbacks(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
agents:
  presets:
    primary: { model: "m1", prompt_file: "", fallbacks: [spare] }
    spare: { model: "m2" }
"""
    )
    runner = get_runner(load(config_path=cfg, env={}))
    assert isinstance(runner, LitellmAgentRunner)
    assert runner._fallback_chains is not None
    assert ("m1", None) in runner._fallback_chains  # keyed by primary endpoint


def test_get_runner_has_no_chains_without_fallbacks(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text('agents:\n  presets:\n    solo: { model: "m1" }\n')
    runner = get_runner(load(config_path=cfg, env={}))
    assert isinstance(runner, LitellmAgentRunner)
    assert runner._fallback_chains is None
