"""The settings page's overlay goes through the loader (docs/plan-model-settings.md
M2, M8, M12): applied after the deploy defaults are merged, then validated by the
very checks a boot runs — so Apply refuses exactly what a boot would."""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.config.loader import load_with_provenance


def _load(tmp_path: Path, overlay: dict | None):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 9090\n", encoding="utf-8")
    settings, _ = load_with_provenance(config_path=cfg, env={}, overlay=overlay)
    return settings


def test_an_overlay_changes_a_preset_and_leaves_the_rest(tmp_path):
    settings = _load(
        tmp_path, {"agents": {"presets": {"openai-mini": {"model": "openai/gpt-4.1-mini"}}}}
    )

    assert settings.agents.presets["openai-mini"].model == "openai/gpt-4.1-mini"
    assert settings.server.port == 9090


def test_null_hides_a_preset_the_deploy_defaults_ship(tmp_path):
    assert "openai-mini" in _load(tmp_path, None).agents.presets

    settings = _load(tmp_path, {"agents": {"presets": {"openai-mini": None}}})

    assert "openai-mini" not in settings.agents.presets


def test_an_overlay_is_refused_by_the_checks_a_boot_runs(tmp_path):
    bad = {"agents": {"presets": {"openai-mini": {"fallbacks": ["no-such-preset"]}}}}

    with pytest.raises(ValueError, match="no-such-preset"):
        _load(tmp_path, bad)
