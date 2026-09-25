"""A plugin's `skill/` becomes a shared skill for agents that can draw views
(#847/#848 PR1 P7).

Eligibility is by capability, read off the RESOLVED tool set: an item whose
tools hold both `write_file` and `show_file` sees plugin skills — not an app
that lists them (editing `app.json` is a source change, which is exactly what a
runtime plugin exists to avoid).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_app.apps import shared_skills
from workspace_app.apps.skills import effective_item_skills
from workspace_app.view_plugins import ViewPluginError, discover_view_plugins
from workspace_app.view_plugins.skills import plugin_skill_sources, register_plugin_skills

DRAWS = ["read_file", "write_file", "show_file"]


def _plugin(root: Path, name: str, *, skill_name: str | None = None, extra: bool = False) -> None:
    d = root / name
    (d / "web").mkdir(parents=True)
    (d / "web" / "index.js").write_text("export {};\n")
    (d / "skill").mkdir()
    (d / "skill" / "SKILL.md").write_text(
        f"---\nname: {skill_name or name}\ndescription: draw a {name} of a table file\n---\n"
        f"Write a `view: {name}` file, then show_file it.\n"
    )
    if extra:
        (d / "skill" / "references").mkdir()
        (d / "skill" / "references" / "marks.md").write_text("marks\n")
    manifest = {"name": name, "sdk": "1", "kinds": [name], "skill": "skill"}
    (d / "plugin.json").write_text(json.dumps(manifest))


@pytest.fixture
def chart(tmp_path: Path):
    _plugin(tmp_path / "plugins", "chart")
    register_plugin_skills(discover_view_plugins(tmp_path / "plugins"))
    yield tmp_path / "plugins" / "chart"
    register_plugin_skills([])


def _states(prefs=None, tools=DRAWS):
    return {
        s.name: s
        for s in effective_item_skills("_template", "default", prefs or {}, [], tools=tools)
    }


def test_an_item_that_can_write_and_show_files_gets_the_plugin_skill(chart):
    s = _states()["chart"]
    assert s.effective and s.default_on
    assert s.description == "draw a chart of a table file"


@pytest.mark.parametrize("missing", ["write_file", "show_file"])
def test_an_item_that_cannot_draw_a_view_does_not(chart, missing):
    assert "chart" not in _states(tools=[t for t in DRAWS if t != missing])


def test_unrestricted_tools_count_as_able(chart):
    assert "chart" in _states(tools=None)


def test_skill_prefs_still_turn_it_off(chart):
    assert _states(prefs={"chart": False})["chart"].effective is False


def test_the_body_is_read_live_so_an_operators_edit_reaches_the_next_read(chart):
    assert "then show_file it" in shared_skills.load_shared_skill("chart")
    (chart / "skill" / "SKILL.md").write_text(
        "---\nname: chart\ndescription: d\n---\nRETUNED guidance\n"
    )
    assert shared_skills.load_shared_skill("chart").strip() == "RETUNED guidance"


def test_unregistering_removes_it(chart):
    register_plugin_skills([])
    assert "chart" not in _states()
    assert shared_skills.shared_skill_source("chart") is None


def test_the_prompt_index_lists_it_for_an_able_item(chart):
    from workspace_app.apps.catalog import AppCatalog
    from workspace_app.config.schema import Settings

    catalog = AppCatalog(presets=Settings().agents.presets)
    cfg = catalog.resolve(app_slug="_template", profile="default")
    assert "- `chart`: draw a chart of a table file" in (cfg.system_prompt or "")
    off = catalog.resolve(app_slug="_template", profile="default", tool_prefs={"show_file": False})
    assert "`chart`" not in (off.system_prompt or "")


def test_a_skill_named_unlike_its_plugin_refuses_boot(tmp_path: Path):
    _plugin(tmp_path / "plugins", "chart", skill_name="charts")
    with pytest.raises(ViewPluginError, match=r"'chart'.*name"):
        plugin_skill_sources(discover_view_plugins(tmp_path / "plugins"))


def test_a_plugin_skill_may_not_shadow_a_shipped_shared_skill(tmp_path: Path):
    _plugin(tmp_path / "plugins", "grill-me")
    with pytest.raises(ViewPluginError, match=r"'grill-me'.*shared skill"):
        plugin_skill_sources(discover_view_plugins(tmp_path / "plugins"))


def test_a_multi_file_plugin_skill_is_a_baked_in_skill_for_materialize(tmp_path: Path):
    """A skill with files beside SKILL.md is copied into the workspace on first
    read and then frozen until Refresh — the existing shared-skill rule, which
    needs `_skill_source` to find the plugin's folder."""
    from workspace_app.apps.skills import _skill_source

    _plugin(tmp_path / "plugins", "chart", extra=True)
    register_plugin_skills(discover_view_plugins(tmp_path / "plugins"))
    try:
        assert _skill_source(None, None, "chart") == ("shared", tmp_path / "plugins/chart/skill")
    finally:
        register_plugin_skills([])


def test_skill_eval_resolves_an_installed_plugin_skill_from_the_config(tmp_path: Path):
    """`--dump-skill` / `--skill` looked names up in SHARED_SKILLS only, so a
    plugin skill was "unknown skill". `register_view_plugins` reads the same
    config a turn does."""
    from workspace_app.skill_eval.__main__ import _resolve_skill, register_view_plugins

    _plugin(tmp_path / "plugins", "chart")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"view_plugins:\n  dir: {tmp_path / 'plugins'}\n")
    try:
        register_view_plugins(cfg)
        name, text, folder = _resolve_skill("chart")
        assert (name, folder) == ("chart", tmp_path / "plugins" / "chart" / "skill")
        assert "then show_file it" in text
    finally:
        register_plugin_skills([])


@pytest.mark.parametrize(("prefs", "listed"), [({}, True), ({"show_file": False}, False)])
def test_the_skills_panel_lists_it_by_the_items_resolved_tools(tmp_path: Path, prefs, listed):
    """The picker's door: `GET .../skills` answers from the same resolve a turn
    composes its prompt from, so an item whose `show_file` is pinned off sees
    neither the prompt line nor the panel row."""
    from fastapi.testclient import TestClient

    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.apps.rca.model import RcaInvestigation
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    _plugin(tmp_path / "plugins", "chart")
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        view_plugins=discover_view_plugins(tmp_path / "plugins"),
    )
    try:
        iid = (
            spec.get_resource_manager(RcaInvestigation)
            .create(RcaInvestigation(title="t", owner="u", attached_tool_prefs=prefs))
            .resource_id
        )
        rows = TestClient(app).get(f"/api/a/rca/items/{iid}/skills").json()["skills"]
        assert ("chart" in {r["name"] for r in rows}) is listed
    finally:
        register_plugin_skills([])


def test_dumping_a_shipped_skill_needs_no_loadable_config(tmp_path: Path):
    """`--dump-skill author-skill` read nothing but the skill before plugins
    were resolvable; a broken config (an unset `${VAR}`) must not stop it."""
    from workspace_app.skill_eval.__main__ import main

    bad = tmp_path / "config.yaml"
    bad.write_text("server:\n  default_user: ${NOT_SET_ANYWHERE_854}\n")
    main(["--dump-skill", "author-skill", "-o", str(tmp_path / "out"), "--config", str(bad)])
    assert (tmp_path / "out" / "SKILL.md").is_file()
