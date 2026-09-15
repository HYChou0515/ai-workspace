"""How the agent comes to know it can put a workflow on a clock.

The knowledge used to live in one skill (`wui`) that no app grants, under a
one-line description about building pages — so an agent asked for a nightly
report answered that no such thing exists. It now sits on the path that agent
actually walks: the `author-workflow` skill every app grants, the sentence
`save_workflow` answers with, and the tool's own description.
"""

from __future__ import annotations

import json

import pytest
from agents import RunContextWrapper

from workspace_app.agent import build_tools
from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import _save_schedules_doc, save_schedules_impl, save_workflow_impl
from workspace_app.apps.catalog import discover_app_slugs
from workspace_app.apps.manifest import load_app_manifest
from workspace_app.apps.shared_skills import load_shared_skill
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow import user_schedules

ALL_APPS = [*discover_app_slugs(), "_template"]


@pytest.mark.parametrize("slug", ALL_APPS)
def test_wherever_save_workflow_is_granted_save_schedules_is_too(slug: str) -> None:
    """One rule, not a list: an app that lets the agent author a workflow lets
    it put that workflow on a clock. A new app copying `_template` inherits both."""
    tools = load_app_manifest(slug).agent.tools
    if "save_workflow" in tools:
        assert "save_schedules" in tools, slug


@pytest.mark.parametrize("slug", [s for s in ALL_APPS if s != "_template"])
def test_the_tool_reaches_the_agents_tool_list(slug: str) -> None:
    manifest = load_app_manifest(slug)
    if "save_workflow" not in manifest.agent.tools:
        pytest.skip("this app does not author workflows")
    names = {
        t.name
        for t in build_tools(manifest.agent.tools, app_slug=slug, profile=manifest.default_profile)
    }
    assert "save_schedules" in names


def test_the_tools_description_is_derived_from_the_validators_periods(monkeypatch) -> None:
    """Not a literal that can go stale: the periods the description offers are
    the ones the validator accepts. Proven by changing the source and watching
    the description follow — a check that only read the current text would pass
    a hand-written copy right up until the day it drifted."""
    for period in user_schedules.EVERY:
        assert f"`{period}`" in (save_schedules_impl.__doc__ or "")
    # What the model is shown IS the builder's output — a hand-written literal
    # that happened to list today's five periods would pass the loop above and
    # drift the day EVERY changes.
    assert save_schedules_impl.__doc__ == _save_schedules_doc()

    monkeypatch.setattr(user_schedules, "EVERY", (*user_schedules.EVERY, "fortnightly"))
    assert "`fortnightly`" in _save_schedules_doc()


def test_the_author_workflow_skill_says_how_to_put_a_workflow_on_a_clock() -> None:
    """The skill every app grants, and the one an agent opens when asked to
    automate something — where "and then run it every night" gets asked."""
    body = load_shared_skill("author-workflow")
    assert "save_schedules" in body
    assert "schedules.json" in body


async def test_saving_a_workflow_points_at_the_clock() -> None:
    """The moment the agent has just made a workflow is the moment "every
    Monday?" comes up. The reply names the next tool rather than leaving it to
    be discovered."""
    ctx = RunContextWrapper(
        AgentToolContext(investigation_id="inv-1", files=WorkspaceFiles(MemoryFileStore()))
    )
    flow = json.dumps(
        {
            "id": "x",
            "title": "T",
            "phases": [{"id": "p"}],
            "steps": [{"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md"}],
        }
    )

    out = await save_workflow_impl(ctx, "nightly", flow)

    assert out.startswith("saved workflow")
    assert "save_schedules" in out
