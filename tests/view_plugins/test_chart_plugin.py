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
    # show_file runs `validate` before it shows a chart file (#854 P9).
    assert m.sandbox.validate is True
    project = tomllib.loads((CHART / "sandbox-src" / "pyproject.toml").read_text())
    # The bundle is installed as /.tools/chart and its launcher execs
    # `.venv/bin/<plugin name>`.
    assert set(project["project"]["scripts"]) == {m.name}
    assert project["tool"]["workspace-tool"]["launch"] == "isolated"
    assert (CHART / "sandbox-src" / "uv.lock").is_file()


def test_it_provides_the_marking_rows_capability_through_a_command_it_has():
    """P7: "save as table" finds the plugin that declares `provides.marking_rows`;
    the platform never names the chart plugin. The command it names is the
    sandbox half's `lit_rows` (listed by the bundle's CLI)."""
    m = _manifest()
    assert m.provides is not None and m.provides.marking_rows == "lit_rows"
    cli = (CHART / "sandbox-src" / "src" / "chart_view" / "cli.py").read_text()
    assert '"lit_rows": LIT_ROWS' in cli


def test_the_views_index_names_the_gallery_and_the_stack():
    # #848's AI side (plan Q2, pr5 P8): "## Available views" gets a line for the
    # gallery and one for a stacked map, or the model never reaches for them.
    whens = " ".join(v.when for v in _manifest().views)
    assert "`facet:`" in whens
    assert "`aggregate`" in whens and "`diff`" in whens


def test_the_csv_table_says_it_follows_a_marking():
    # pr5 P1/P8: a table joins a linked selection, and the model has to know.
    table = msgspec.json.decode(
        (CHART.parent / "csv-table" / "plugin.json").read_bytes(), type=PluginManifest
    )
    assert [v.kind for v in table.views] == ["csv-table"]
    assert "`marking:`" in table.views[0].when


def test_every_scenario_loads_and_ships_its_data():
    # The operator's retuning kit (plan Q19): a scenario whose data file is
    # missing fails at stage time on the operator's machine, not here.
    from workspace_app.skill_eval.scenario import load_scenarios

    folder = CHART / "scenarios"
    scenarios = load_scenarios(folder)
    for s in scenarios:
        for name in s.data:
            assert (folder / name).is_file(), (s.name, name)
    # pr5 P8: one per feature the model can show or read.
    names = {s.name for s in scenarios}
    assert {"gallery-alike-shown", "stack-shared-shown", "linked-table-shown"} <= names
    assert "saved-selection-read" in names
