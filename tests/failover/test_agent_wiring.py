"""Agent-path failover wiring (#196): a preset's fallback chain makes the agent
turn run on a busy-aware FallbackModel; single-endpoint configs are untouched."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agents.models.interface import Model

from workspace_app.api.litellm_runner import LitellmAgentRunner, _agent_for
from workspace_app.config.loader import load
from workspace_app.factories import LlmEndpoint, get_runner
from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.failover.model import FallbackModel
from workspace_app.resources import AgentConfig

FallbackChains = dict[tuple[str, str | None], list[LlmEndpoint]]


class _FakeModel(Model):
    """A network-free SDK model: stream_response raises ``error`` or yields events."""

    def __init__(self, *, events: list[Any] | None = None, error: Exception | None = None) -> None:
        self._events = events or []
        self._error = error

    async def get_response(self, *args, **kwargs):  # pragma: no cover — unused here
        raise AssertionError

    async def stream_response(self, *args, **kwargs) -> AsyncIterator[Any]:
        if self._error is not None:
            raise self._error
        for ev in self._events:
            yield ev


async def _collect(agen) -> list:
    return [x async for x in agen]


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


async def test_agent_for_emits_a_failover_switch_notice_on_a_pre_first_token_switch():
    """#249/#131: when the chat model switches before its first token, the wired
    on_failover_switch fires with the failed model + cause type — the FE's
    transient 'model busy, switched' notice."""
    impls = {
        "m1": _FakeModel(error=RuntimeError("502 bad gateway")),  # blips pre-first-token
        "m2": _FakeModel(events=["answer"]),  # the spare answers
    }
    notices: list[tuple[str, str]] = []

    cfg = AgentConfig(name="a", model="m1", llm_base_url="http://a")
    chains: FallbackChains = {("m1", "http://a"): [_ep("m1", "http://a"), _ep("m2", "http://b")]}
    reg = CooldownRegistry(clock=lambda: 0.0)
    agent = _agent_for(
        cfg,
        fallback_chains=chains,
        cooldown_registry=reg,
        on_failover_switch=lambda model, reason: notices.append((model, reason)),
    )
    assert isinstance(agent.model, FallbackModel)
    agent.model._make_model = lambda e: impls[e.model]  # noqa: SLF001 — swap in network-free fakes

    out = await _collect(agent.model.stream_response())
    assert out == ["answer"]  # switched to the spare and streamed its answer
    assert notices == [("m1", "RuntimeError")]  # failed model + cause surfaced to the FE


async def test_agent_for_switch_without_a_notice_hook_still_streams():
    """A switch with no on_failover_switch wired (e.g. the non-stream/replay
    paths) just logs and carries on — no crash, the spare still answers."""
    impls = {"m1": _FakeModel(error=RuntimeError("boom")), "m2": _FakeModel(events=["answer"])}
    cfg = AgentConfig(name="a", model="m1", llm_base_url="http://a")
    chains: FallbackChains = {("m1", "http://a"): [_ep("m1", "http://a"), _ep("m2", "http://b")]}
    reg = CooldownRegistry(clock=lambda: 0.0)
    agent = _agent_for(cfg, fallback_chains=chains, cooldown_registry=reg)  # no on_failover_switch
    assert isinstance(agent.model, FallbackModel)
    agent.model._make_model = lambda e: impls[e.model]  # noqa: SLF001
    assert await _collect(agent.model.stream_response()) == ["answer"]


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
