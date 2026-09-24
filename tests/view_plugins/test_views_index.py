"""`## Available views` (#847/#848 PR1 P8): one line per plugin `views` entry,
for the items that can draw a view, beside `## Available skills`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_app.apps.catalog import AppCatalog
from workspace_app.config.schema import Settings
from workspace_app.view_plugins import discover_view_plugins
from workspace_app.view_plugins.skills import register_for_agents


def _plugin(root: Path, name: str, views: list[dict]) -> None:
    d = root / name
    (d / "web").mkdir(parents=True)
    (d / "web" / "index.js").write_text("export {};\n")
    kinds = sorted({v["kind"] for v in views}) or [name]
    (d / "plugin.json").write_text(
        json.dumps({"name": name, "sdk": "1", "kinds": kinds, "views": views})
    )


@pytest.fixture
def plugins(tmp_path: Path):
    _plugin(
        tmp_path / "p",
        "chart",
        [
            {"kind": "chart", "when": "numbers across categories or over time, from a table file"},
            {"kind": "chart-grid", "when": "a value laid out on a 2-D lattice"},
        ],
    )
    _plugin(tmp_path / "p", "quiet", [])
    register_for_agents(discover_view_plugins(tmp_path / "p"))
    yield
    register_for_agents([])


def _prompt(**kw) -> str:
    cfg = AppCatalog(presets=Settings().agents.presets).resolve(
        app_slug="_template", profile="default", **kw
    )
    return cfg.system_prompt or ""


def test_lists_one_line_per_views_entry(plugins):
    prompt = _prompt()
    assert "## Available views" in prompt
    assert "- `chart`: numbers across categories or over time, from a table file" in prompt
    assert "- `chart-grid`: a value laid out on a 2-D lattice" in prompt
    # It says how to use them, and says it as a capability.
    section = prompt[prompt.index("## Available views") :]
    assert "`show_file`" in section.split("\n\n- ")[0] or "`show_file`" in section
    for word in ("don't", "never", "not "):
        assert word not in section.lower()


def test_absent_for_an_item_that_cannot_show_files(plugins):
    assert "## Available views" not in _prompt(tool_prefs={"show_file": False})


def test_absent_when_no_plugin_declares_a_view(tmp_path: Path):
    _plugin(tmp_path / "p", "quiet", [])
    register_for_agents(discover_view_plugins(tmp_path / "p"))
    try:
        assert "## Available views" not in _prompt()
    finally:
        register_for_agents([])


def test_absent_with_no_plugins():
    register_for_agents([])
    assert "## Available views" not in _prompt()
