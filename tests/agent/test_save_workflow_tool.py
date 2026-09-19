"""#323 — `save_workflow(id, workflow_json)`: validate a user-authored workflow.json
and write it into the workspace `.workflows/`, or hand the problems back to fix."""

from __future__ import annotations

import json

from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import save_workflow_impl
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.workspace_store import workspace_workflow_metas

_VALID = json.dumps(
    {
        "id": "x",
        "phases": [{"id": "note"}],
        "steps": [
            {"type": "agent", "cache": True, "prompt": "hi", "phase": "note", "out": "note.md"}
        ],
    }
)


def _ctx(**over):
    files = WorkspaceFiles(MemoryFileStore())
    return RunContextWrapper(AgentToolContext(investigation_id="inv-1", files=files, **over))


async def test_valid_workflow_saves_and_lists():
    ctx = _ctx()
    out = await save_workflow_impl(ctx, "My Flow", _VALID)
    assert "saved workflow 'my-flow'" in out
    metas = await workspace_workflow_metas(ctx.context.files, ctx.context.investigation_id)
    assert [m.id for m in metas] == ["my-flow"]


async def test_invalid_workflow_returns_problems_and_is_not_saved():
    ctx = _ctx()
    bad = json.dumps(
        {
            "id": "x",
            "phases": [{"id": "note"}],
            "steps": [{"type": "sandbox", "cache": True, "run": "x", "phase": "zz"}],
        }
    )
    out = await save_workflow_impl(ctx, "flow", bad)
    assert out.startswith("error: the workflow has problems") and "not declared" in out
    assert await workspace_workflow_metas(ctx.context.files, ctx.context.investigation_id) == []


async def test_unparseable_workflow_is_an_error():
    out = await save_workflow_impl(_ctx(), "flow", "{not json")
    assert "error" in out and "won't parse" in out


async def test_no_workspace_context_is_a_friendly_error():
    ctx = RunContextWrapper(AgentToolContext())
    assert "error" in await save_workflow_impl(ctx, "flow", _VALID)


async def test_id_with_no_usable_chars_rejected():
    assert "error" in await save_workflow_impl(_ctx(), "!!!", _VALID)


async def test_tool_ceiling_clamps_agent_tools():
    """With an App/profile context, an agent step asking for a tool outside the profile's
    ceiling (topic-hub has no `exec`) is rejected at save time (Q4)."""
    ctx = _ctx(app_slug="topic-hub", template_profile="default")
    over = json.dumps(
        {
            "id": "x",
            "phases": [{"id": "note"}],
            "steps": [
                {
                    "type": "agent",
                    "cache": True,
                    "prompt": "p",
                    "phase": "note",
                    "out": "o",
                    "tools": ["exec"],
                }
            ],
        }
    )
    out = await save_workflow_impl(ctx, "flow", over)
    assert "error" in out and "outside the profile's allowed tools" in out


async def test_unknown_app_skips_the_tool_clamp():
    """A synthetic / unreadable slug yields no ceiling — validation proceeds without the
    tool clamp rather than crashing."""
    ctx = _ctx(app_slug="no-such-app-xyz", template_profile="whatever")
    assert "saved workflow" in await save_workflow_impl(ctx, "flow", _VALID)


async def test_profile_tools_override_is_the_ceiling():
    """A profile that narrows the App's tools (playground/intake) is the ceiling — a
    tools-free workflow still saves under it (exercises the profile-override path)."""
    ctx = _ctx(app_slug="playground", template_profile="intake")
    assert "saved workflow" in await save_workflow_impl(ctx, "flow", _VALID)


async def test_confirmation_names_a_path_the_agent_can_actually_use():
    """Same contract as save_skill: the confirmation is where the agent learns
    where its workflow landed, and `exec` has no chroot — so the reported path
    is the relative form its own `list_files` prints, and it round-trips."""
    from workspace_app.agent.tools import exists_impl

    ctx = _ctx()
    out = await save_workflow_impl(ctx, "My Flow", _VALID)
    assert ".workflows/my-flow.json" in out
    assert "/.workflows/my-flow.json" not in out
    assert await exists_impl(ctx, ".workflows/my-flow.json") is True


def _steps_with_tools(tools: list[str]) -> str:
    return json.dumps(
        {
            "id": "x",
            "phases": [{"id": "note"}],
            "steps": [
                {
                    "type": "agent",
                    "cache": True,
                    "prompt": "p",
                    "phase": "note",
                    "out": "o",
                    "tools": tools,
                }
            ],
        }
    )


def _rca_pkg():
    from workspace_app.tooling.registry import CommandInfo, PackageInfo

    return PackageInfo(
        name="rca-tools",
        install_dir="../.tools/rca-tools",
        commands=tuple(CommandInfo(c, f"{c}.", {}) for c in ("spc", "pareto")),
    )


async def test_a_command_of_a_package_the_profile_grants_whole_is_accepted_by_name():
    """plan-tools-picker-groups part 2: the run honours `rca-tools:spc` on an
    item whose App grants `rca-tools` whole, so the validator accepts it —
    judged against the ceiling brought to COMMAND granularity with the turn's
    own package list, which is what makes the next test possible."""
    ctx = _ctx(app_slug="rca", template_profile="default", packages=[_rca_pkg()])
    out = await save_workflow_impl(ctx, "flow", _steps_with_tools(["rca-tools:spc", "read_file"]))
    assert "saved workflow" in out, out


async def test_a_command_the_package_does_not_have_is_refused_not_saved():
    """`rca-tools:spcc` is a typo. Accepting it saves a workflow whose node
    silently holds nothing of the package at run time; the validator's whole
    job is to say so now. The packages are in hand at this door, so the
    ceiling is expanded and the typo names nothing in it."""
    ctx = _ctx(app_slug="rca", template_profile="default", packages=[_rca_pkg()])
    out = await save_workflow_impl(ctx, "flow", _steps_with_tools(["rca-tools:spcc"]))
    assert "error" in out and "outside the profile's allowed tools" in out, out
    out = await save_workflow_impl(ctx, "flow2", _steps_with_tools(["exec:foo"]))
    assert "error" in out and "outside the profile's allowed tools" in out, out
