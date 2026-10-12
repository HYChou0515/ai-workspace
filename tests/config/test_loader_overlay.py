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


def test_a_key_lands_only_where_its_setting_exists(tmp_path):
    # review r2: a key was written into the patch, creating whatever parents it
    # named — so a key for a preset the version did not have (a rollback to an
    # older version) made a half preset the loader refused, and rollback with it.
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 9090\n", encoding="utf-8")
    keys = [
        (["agents", "presets", "openai-mini", "llm", "api_key"], "sk-mini"),
        (["agents", "presets", "gone", "llm", "api_key"], "sk-gone"),
    ]

    settings, _ = load_with_provenance(
        config_path=cfg,
        env={},
        overlay={"agents": {"presets": {"openai-mini": {"model": "openai/x"}}}},
        keys=keys,
    )

    assert settings.agents.presets["openai-mini"].llm.api_key == "sk-mini"
    assert "gone" not in settings.agents.presets


def test_kb_quality_judge_is_built(tmp_path):
    # final review 4: the loader validated `kb.quality_judge` and never built it,
    # so the judge always fell back to `retrieval_llm` — from config.yaml and
    # from the settings page alike.
    from workspace_app.config.loader import load

    config = tmp_path / "config.yaml"
    config.write_text(
        "kb:\n  quality_judge:\n    preset: kb-default\n    model: openai/judge\n",
        encoding="utf-8",
    )

    judge = load(config_path=config, env={}).kb.quality_judge

    assert judge is not None and judge.preset == "kb-default"


def test_a_key_lands_under_a_role_whose_config_yaml_says_llm_null(tmp_path):
    # final review 10: `llm: null` is a valid role reference (no per-ref creds);
    # the key loop kept the None, dropped the key and the page called the role
    # missing.
    config = tmp_path / "config.yaml"
    config.write_text(
        "kb:\n  retrieval_llm:\n    preset: kb-default\n    llm: null\n", encoding="utf-8"
    )

    settings, _ = load_with_provenance(
        config_path=config, env={}, keys=[(["kb", "retrieval_llm", "llm", "api_key"], "sk-x")]
    )

    assert settings.kb.retrieval_llm is not None
    assert settings.kb.retrieval_llm.llm.api_key == "sk-x"
