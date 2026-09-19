"""`--preset` names the turn the guidance is measured under. `AppCatalog.resolve`
honours an attached preset only when the App's picker (or the profile's
subset) lists it and otherwise falls back to the default WITHOUT saying so —
right for a live turn, where the picker is the whole point, but a tuning run
that silently measures a different model reports a number about nothing:
`--preset qwen3-8b-eval` ran `qwen3:14b` for forty minutes and the report
header was the only place that said so."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from workspace_app.config.schema import Preset, PresetLlmSettings
from workspace_app.resources.agent_config import AgentConfig
from workspace_app.skill_eval.__main__ import _resolve_agent

PRESETS = {
    "in-picker": Preset(model="ollama_chat/a", llm=PresetLlmSettings(base_url="http://a")),
    "declared-only": Preset(model="ollama_chat/b"),
}


def _wire(monkeypatch, resolved: AgentConfig) -> None:
    settings = SimpleNamespace(agents=SimpleNamespace(presets=PRESETS))
    monkeypatch.setattr("workspace_app.config.loader.load", lambda config_path=None: settings)
    catalog = SimpleNamespace(resolve=lambda **kw: resolved)
    monkeypatch.setattr("workspace_app.factories.get_app_catalog", lambda s: catalog)


def test_a_preset_the_picker_honours_is_returned(monkeypatch):
    cfg = AgentConfig(name="A", model="ollama_chat/a", llm_base_url="http://a")
    _wire(monkeypatch, cfg)
    assert _resolve_agent("rca", "default", "in-picker", None) is cfg


def test_no_preset_means_the_profile_default_and_no_check(monkeypatch):
    cfg = AgentConfig(name="Default", model="ollama_chat/whatever")
    _wire(monkeypatch, cfg)
    assert _resolve_agent("rca", "default", None, None) is cfg


def test_a_declared_preset_the_picker_ignores_is_refused_not_swapped(monkeypatch):
    """The catalog resolved the DEFAULT preset's model instead of the named
    one: the run would measure a model the report never asked for."""
    _wire(monkeypatch, AgentConfig(name="Default", model="ollama_chat/a", llm_base_url="http://a"))
    with pytest.raises(SystemExit) as e:
        _resolve_agent("rca", "default", "declared-only", None)
    assert "declared-only" in str(e.value) and "picker" in str(e.value)


def test_an_unknown_preset_is_refused_with_the_known_names(monkeypatch):
    _wire(monkeypatch, AgentConfig(name="Default", model="ollama_chat/a"))
    with pytest.raises(SystemExit) as e:
        _resolve_agent("rca", "default", "no-such", None)
    assert "no-such" in str(e.value) and "in-picker" in str(e.value)
