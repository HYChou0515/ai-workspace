"""Every test here may register plugins for agents (skills, `## Available
views`, the kinds `show_file` validates) — module-level registries, like
`SHARED_SKILLS`. Reset them after each test so none leaks into the next one on
the same worker (a prompt composed later would grow an `## Available views`
section depending on test order)."""

from __future__ import annotations

import pytest

from workspace_app.apps import shared_skills
from workspace_app.view_plugins.skills import register_for_agents


@pytest.fixture(autouse=True)
def _no_plugin_registrations_leak():
    yield
    register_for_agents([])
    assert not shared_skills.PLUGIN_SKILLS
    assert not shared_skills.PLUGIN_VIEWS
    assert not shared_skills.PLUGIN_KINDS
