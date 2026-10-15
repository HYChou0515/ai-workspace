"""docs/plan-model-settings.md P5 — the API's consumers read the published
settings when they are used, not the boot snapshot.

Each test boots a consumer from the deploy defaults, publishes a change, moves
the clock past the version read, and asks the consumer again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.config.loader import load
from workspace_app.factories import get_kb_describer, get_kb_llm, get_runner
from workspace_app.model_settings.consumers import (
    LiveLlm,
    live_agent_config_catalog,
)
from workspace_app.model_settings.live import LiveSettings
from workspace_app.model_settings.store import SettingsStore, register_settings_models
from workspace_app.resources import make_spec


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  port: 9090\n", encoding="utf-8")
    return path


@pytest.fixture
def store() -> SettingsStore:
    spec = make_spec()
    register_settings_models(spec)
    return SettingsStore(spec)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def live(store, config, clock) -> LiveSettings:
    boot = load(config_path=config, env={})
    return LiveSettings(store, config_path=config, env={}, boot=boot, clock=clock)


def _apply(store, clock, patch):
    store.publish(patch, by="root")
    clock.now += 2


FALLBACK = {
    "config": {
        "agents": {
            "presets": {
                "fast": {"model": "openai/fast-1", "fallbacks": ["slow"]},
                "slow": {"model": "openai/slow-1"},
            }
        }
    }
}


def test_a_fallback_added_after_boot_is_failed_over_to(live, store, clock):
    runner = get_runner(live.settings(), live=live)
    assert isinstance(runner, LitellmAgentRunner)
    assert ("openai/fast-1", None) not in (runner._fallback_chains or {})

    _apply(store, clock, FALLBACK)

    chain = (runner._fallback_chains or {})[("openai/fast-1", None)]
    assert [e.model for e in chain] == ["openai/fast-1", "openai/slow-1"]
    # A deploy with no fallbacks at boot still gets the cooldown registry, or
    # the chain above could never be switched along.
    assert runner._cooldown_registry is not None


def test_the_runner_default_endpoint_follows_the_published_settings(live, store, clock):
    runner = get_runner(live.settings(), live=live)
    assert isinstance(runner, LitellmAgentRunner)

    _apply(store, clock, {"config": {"llm": {"base_url": "http://gw:4000"}}})

    assert runner._base_url == "http://gw:4000"


@pytest.fixture
def echo_model(monkeypatch):
    """Every LiteLLM client answers with the model it was built for — what a
    role LLM call reaches, without a network."""
    from workspace_app.kb.llm import LitellmLlm
    from workspace_app.kb.vlm.litellm import LitellmVlm

    monkeypatch.setattr(LitellmLlm, "stream", lambda self, prompt: iter([(self._model, False)]))
    monkeypatch.setattr(
        LitellmVlm, "stream", lambda self, prompt, *, images: iter([(self._model, False)])
    )


def _retrieval(model):
    preset = live_settings_preset_of_retrieval()
    return {"config": {"agents": {"presets": {preset: {"model": model}}}}}


def live_settings_preset_of_retrieval() -> str:
    ref = load(config_path=None, env={}).kb.retrieval_llm
    assert ref is not None and ref.preset
    return ref.preset


def test_a_role_llm_calls_the_published_model(live, store, clock, echo_model):
    llm = get_kb_llm(live.settings(), live=live)
    assert llm is not None
    assert llm.collect("q") != "openai/retrieval-2"

    _apply(store, clock, _retrieval("openai/retrieval-2"))

    assert llm.collect("q") == "openai/retrieval-2"


def test_a_role_client_is_kept_while_its_endpoints_are_unchanged(live, store, clock, echo_model):
    llm = get_kb_llm(live.settings(), live=live)
    assert isinstance(llm, LiveLlm)
    client = llm.current()

    _apply(store, clock, {"config": {"agents": {"presets": {"kb-claude": {"model": "x/y"}}}}})
    assert llm.current() is client  # another preset changed: same client (M14)

    _apply(store, clock, _retrieval("openai/retrieval-2"))
    assert llm.current() is not client


def test_a_vision_role_calls_the_published_model(live, store, clock, echo_model):
    describer = get_kb_describer(live.settings(), live=live)
    assert describer is not None
    vlm_preset = live.settings().kb.vlm_llm
    assert vlm_preset is not None and vlm_preset.preset

    _apply(store, clock, {"config": {"agents": {"presets": {vlm_preset.preset: {"model": "v/2"}}}}})

    assert describer._vlm.collect("q", images=[]) == "v/2"


def test_the_kb_chat_list_follows_the_published_settings(live, store, clock):
    catalog = live_agent_config_catalog(live)
    assert catalog.kb_chats()[0].model != "openai/kb-2"

    _apply(
        store, clock, {"config": {"agents": {"presets": {"kb-default": {"model": "openai/kb-2"}}}}}
    )

    assert catalog.kb_chats()[0].model == "openai/kb-2"
    default = catalog.default_for("kb_chat")
    assert default is not None and default.model == "openai/kb-2"
    assert catalog.presets()["kb-default"].model == "openai/kb-2"
