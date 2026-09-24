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


def test_a_plugin_only_local_deploy_mounts_its_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`PACKAGES` empty used to mean "no /.tools" outright (`__main__`); a plugin
    bundle must still reach the sandbox."""
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    monkeypatch.setattr("workspace_app.__main__.PREBUILT_DIR", tmp_path / "tools")
    _plugin(tmp_path / "plugins", "sb", sandbox={"bundle": "sandbox"})
    bundle = tmp_path / "plugins" / "sb" / "sandbox"
    bundle.mkdir()
    (bundle / "launch").write_text("#!/bin/sh\necho hi\n")
    (bundle / "launch").chmod(0o755)
    seen: dict = {}
    real = __import__("workspace_app.__main__", fromlist=["get_sandbox"]).get_sandbox

    def spy(settings, tools_dir=None):
        seen["tools_dir"] = tools_dir
        return real(settings, tools_dir=tools_dir)

    monkeypatch.setattr("workspace_app.__main__.get_sandbox", spy)
    build_app(_settings(tmp_path, tmp_path / "plugins"), config_dir=None)
    assert seen["tools_dir"] == tmp_path / "tools-with-view-plugins"
    assert (seen["tools_dir"] / "sb" / "launch").is_file()


def test_plugin_commands_never_become_agent_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A plugin bundle is a standard tool bundle — `commands.json` and all — and
    it sits in the same `/.tools` root as the packages. It must still reach no
    app's tool ceiling: the catalog the agent and the picker read omits it."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    monkeypatch.setattr("workspace_app.__main__.PREBUILT_DIR", tmp_path / "tools")
    _plugin(tmp_path / "plugins", "sb", sandbox={"bundle": "sandbox"})
    bundle = tmp_path / "plugins" / "sb" / "sandbox"
    (bundle / "schemas").mkdir(parents=True)
    (bundle / "launch").write_text("#!/bin/sh\n")
    (bundle / "launch").chmod(0o755)
    (bundle / "commands.json").write_text(json.dumps([{"name": "summary"}]))
    (bundle / "schemas" / "summary.json").write_text(
        json.dumps({"name": "summary", "description": "d", "params_json_schema": {}})
    )
    app = build_app(_settings(tmp_path, tmp_path / "plugins"), config_dir=None)
    names = {t["name"] for t in TestClient(app).get("/api/tools").json()}
    assert "summary" not in names
