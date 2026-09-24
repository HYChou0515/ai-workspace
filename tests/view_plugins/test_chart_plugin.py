"""The chart plugin's source tree matches what `view_plugin build` installs
(#855): its plugin.json is a manifest discovery accepts, and each part the
manifest names has a source."""

from __future__ import annotations

import tomllib
from pathlib import Path

import msgspec

from workspace_app.view_plugins.manifest import PluginManifest

CHART = Path(__file__).resolve().parents[2] / "view-plugins" / "chart"


def _manifest() -> PluginManifest:
    return msgspec.json.decode((CHART / "plugin.json").read_bytes(), type=PluginManifest)


def test_the_manifest_is_one_discovery_accepts():
    m = _manifest()
    assert m.name == CHART.name == "chart"
    assert m.kinds == ["chart"]
    assert {v.kind for v in m.views} == {"chart"}


def test_the_skill_is_one_file():
    # A SKILL.md-only skill is read live, so an operator's edit reaches the next
    # turn (plan check 5); a second file would freeze it at first read.
    m = _manifest()
    assert m.skill is not None
    assert sorted(p.name for p in (CHART / m.skill).iterdir()) == ["SKILL.md"]


def test_the_sandbox_half_is_built_from_sandbox_src_under_the_isolated_launcher():
    m = _manifest()
    assert m.sandbox is not None and m.sandbox.bundle == "sandbox"
    project = tomllib.loads((CHART / "sandbox-src" / "pyproject.toml").read_text())
    # The bundle is installed as /.tools/chart and its launcher execs
    # `.venv/bin/<plugin name>`.
    assert set(project["project"]["scripts"]) == {m.name}
    assert project["tool"]["workspace-tool"]["launch"] == "isolated"
    assert (CHART / "sandbox-src" / "uv.lock").is_file()
