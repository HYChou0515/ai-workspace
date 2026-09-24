"""The API's composition root discovers view plugins at boot (#847/#848 P2):
a malformed plugin refuses boot by name, a good one reaches `app.state`."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from workspace_app.__main__ import build_app
from workspace_app.config.loader import load
from workspace_app.view_plugins import ViewPluginError


def _settings(tmp_path: Path, plugins: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent(f"""
            sandbox:
              root: {tmp_path / "sandbox"}
            kb:
              embedder:
                model: ""
            view_plugins:
              dir: {plugins}
        """),
        encoding="utf-8",
    )
    return load(config_path=cfg, env={})


def _plugin(root: Path, name: str, **extra) -> None:
    d = root / name
    (d / "web").mkdir(parents=True)
    (d / "web" / "index.js").write_text("export {};\n")
    (d / "plugin.json").write_text(json.dumps({"name": name, "sdk": "1", "kinds": [name], **extra}))


def test_a_malformed_plugin_refuses_boot_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    _plugin(tmp_path / "plugins", "wobbly", colour="red")
    with pytest.raises(ViewPluginError, match="'wobbly'.*colour"):
        build_app(_settings(tmp_path, tmp_path / "plugins"), config_dir=None)


def test_discovered_plugins_reach_the_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    _plugin(tmp_path / "plugins", "outside-the-repo")
    app = build_app(_settings(tmp_path, tmp_path / "plugins"), config_dir=None)
    assert [p.name for p in app.state.view_plugins] == ["outside-the-repo"]
