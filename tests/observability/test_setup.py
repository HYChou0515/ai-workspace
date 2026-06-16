"""`observability.setup` — gate + install the LLM call logger.

Gating: config `observability.llm_log.enabled` (default on), overridable by the
`WORKSPACE_LLM_LOG` env var (the prod off-switch). Install registers one
`LlmCallLogger` into `litellm.callbacks` when enabled.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from workspace_app.config.loader import load
from workspace_app.observability.logger import LlmCallLogger
from workspace_app.observability.setup import install_llm_logging, llm_log_enabled


def _disabled_settings(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            observability:
              llm_log:
                enabled: false
        """),
        encoding="utf-8",
    )
    return load(config_path=cfg, env={})


def test_enabled_falls_back_to_config_when_env_unset():
    settings = load(config_path=None, env={})  # config default = enabled
    assert llm_log_enabled(settings, {}) is True


def test_env_var_overrides_config_off():
    settings = load(config_path=None, env={})  # config enabled
    assert llm_log_enabled(settings, {"WORKSPACE_LLM_LOG": "0"}) is False


def test_env_var_overrides_config_on(tmp_path: Path):
    settings = _disabled_settings(tmp_path)  # config disabled
    assert llm_log_enabled(settings, {"WORKSPACE_LLM_LOG": "1"}) is True


def test_install_registers_logger_when_enabled(tmp_path: Path, monkeypatch):
    import litellm

    monkeypatch.setattr(litellm, "callbacks", [])
    settings = load(config_path=None, env={})
    logger = install_llm_logging(settings, env={}, root=tmp_path / "llm")
    assert isinstance(logger, LlmCallLogger)
    assert logger in litellm.callbacks


def test_install_is_noop_when_disabled(tmp_path: Path, monkeypatch):
    import litellm

    monkeypatch.setattr(litellm, "callbacks", [])
    settings = load(config_path=None, env={})
    logger = install_llm_logging(settings, env={"WORKSPACE_LLM_LOG": "0"}, root=tmp_path / "llm")
    assert logger is None
    assert litellm.callbacks == []
