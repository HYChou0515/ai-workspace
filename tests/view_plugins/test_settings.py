"""`view_plugins:` config section + where the plugin dir resolves (#847/#848 P2)."""

from __future__ import annotations

import re
from pathlib import Path
from textwrap import dedent

import pytest

from workspace_app.config.loader import load
from workspace_app.config.schema import ViewPluginsSettings
from workspace_app.view_plugins import BUILTIN_VIEW_KINDS
from workspace_app.view_plugins.discovery import DEFAULT_PLUGINS_DIR, resolve_plugins_dir

_REPO = Path(__file__).resolve().parents[2]


def test_section_loads_and_is_built(tmp_path: Path):
    """Whitelisted AND built — a key that parses but never reaches Settings is
    the dead-knob class."""
    assert load(config_path=tmp_path / "missing.yaml", env={}).view_plugins.dir == ""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
        view_plugins:
          dir: /opt/plugins
    """)
    )
    assert load(config_path=cfg, env={}).view_plugins.dir == "/opt/plugins"


def test_an_unknown_key_refuses_to_load(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("view_plugins:\n  path: /x\n")
    with pytest.raises(ValueError, match="view_plugins"):
        load(config_path=cfg, env={})


def test_default_is_the_repo_dot_view_plugins():
    assert DEFAULT_PLUGINS_DIR == _REPO / ".view-plugins"
    assert resolve_plugins_dir(ViewPluginsSettings(), env={}) == DEFAULT_PLUGINS_DIR


def test_env_overrides_the_default():
    got = resolve_plugins_dir(ViewPluginsSettings(), env={"WORKSPACE_VIEW_PLUGINS_DIR": "/env/p"})
    assert got == Path("/env/p")


def test_config_wins_over_env():
    env = {"WORKSPACE_VIEW_PLUGINS_DIR": "/env/p"}
    got = resolve_plugins_dir(ViewPluginsSettings(dir="/cfg/p"), env=env)
    assert got == Path("/cfg/p")


def test_builtin_kinds_match_the_spa():
    """Parity with `VIEW_KIND` in the SPA — the oracle is the TS file itself."""
    text = (_REPO / "web/src/renderers/entity/types.ts").read_text()
    block = re.search(r"export const VIEW_KIND = \{(.*?)\n\} as const", text, re.S)
    assert block, "VIEW_KIND block not found in types.ts"
    spa = set(re.findall(r'^\s*(\w+): "\1",', block.group(1), re.M))
    assert spa == BUILTIN_VIEW_KINDS
