"""`read_skill("wui")` names the sub-agents a page may ask (#942).

Through the tool itself: the names come from the turn's own `subagent_defs` —
the list its `run_agent` delegates to — so what the skill tells the model a page
may ask and what `callAgent` admits are one list, not two kept alike.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import read_skill_impl
from workspace_app.apps.subagents import SubagentDef
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _ctx(defs: tuple[SubagentDef, ...]) -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=WorkspaceFiles(MemoryFileStore()),
            app_slug="rca",
            template_profile="default",
            acting_user="u",
            subagent_defs=defs,
        )
    )


async def test_the_wui_skill_names_this_turns_sub_agents():
    body = await read_skill_impl(_ctx((SubagentDef(name="log-digger", description="digs"),)), "wui")
    assert "Sub-agents this item offers its WUIs" in body
    assert "- `log-digger`" in body


async def test_an_item_with_none_is_told_to_leave_agents_out():
    body = await read_skill_impl(_ctx(()), "wui")
    assert "leave `agents:` out" in body


async def test_without_a_workspace_the_baked_in_skill_names_them_too():
    """No workspace to copy the skill into: the baked-in branch answers, and
    must say the same thing."""
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id=None,
            app_slug="rca",
            template_profile="default",
            acting_user="u",
            subagent_defs=(SubagentDef(name="log-digger", description="digs"),),
        )
    )
    body = await read_skill_impl(ctx, "wui")
    assert "- `log-digger`" in body
