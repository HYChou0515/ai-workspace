"""`show_skill_hub_entry` is listed under `## Available views`
(docs/plan-skill-hub-history.md §8, A3): the AI is told the card exists, for
the turns that hold the tool — and only those."""

from __future__ import annotations

from workspace_app.apps.catalog import AppCatalog
from workspace_app.config.schema import Settings
from workspace_app.view_plugins.skills import register_for_agents


def _prompt(**kw) -> str:
    register_for_agents([])  # no view plugin: the line stands on its own
    cfg = AppCatalog(presets=Settings().agents.presets).resolve(
        app_slug="rca", profile="default", **kw
    )
    return cfg.system_prompt or ""


def test_a_turn_with_the_tool_is_told_the_card_exists() -> None:
    prompt = _prompt()
    section = prompt[prompt.index("## Available views") :]
    assert "`show_skill_hub_entry(entry_id)`" in section
    for word in ("don't", "never", "not "):
        assert word not in section.lower()


def test_a_turn_without_it_is_not() -> None:
    assert "show_skill_hub_entry" not in _prompt(tool_prefs={"show_skill_hub_entry": False})
