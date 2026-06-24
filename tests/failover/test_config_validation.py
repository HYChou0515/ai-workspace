"""Loader validation for preset failover chains (#196)."""

from __future__ import annotations

import pytest

from workspace_app.config.loader import load


def _load(tmp_path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(body)
    return load(config_path=p, env={})


def test_unknown_fallback_preset_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown preset 'ghost'"):
        _load(
            tmp_path,
            """
agents:
  presets:
    primary: { model: "m", fallbacks: [ghost] }
""",
        )


def test_preset_listing_itself_as_a_fallback_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="lists itself"):
        _load(
            tmp_path,
            """
agents:
  presets:
    primary: { model: "m", fallbacks: [primary] }
""",
        )


def test_valid_fallback_chain_loads(tmp_path):
    settings = _load(
        tmp_path,
        """
agents:
  presets:
    primary: { model: "m", fallbacks: [spare] }
    spare: { model: "m2" }
""",
    )
    assert settings.agents.presets["primary"].fallbacks == ["spare"]
