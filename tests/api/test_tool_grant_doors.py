"""plan-tools-picker-groups part 2 (P14 revision): the tool grant is finalized
at the DOORS where a resolved config meets its package list, and every reader
downstream of a door sees the same command-granular answer.

Each test walks a real door — the chat turn builder, a workflow step, the
compaction summariser, a sub-agent's child context — and looks at what the
runner would register (`_agent_for`, the same call the live runner makes) or
at the context's own `allowed_tools`. Nothing here calls `command_grants`
directly: the rule's own tests are in `tests/tooling/test_command_grants.py`.
"""

from __future__ import annotations

from typing import Any

import workspace_app.api.app as app_mod
from workspace_app.api import create_app
from workspace_app.api.compaction import AgentCompactor
from workspace_app.api.events import RunDone
from workspace_app.api.litellm_runner import _agent_for
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.resolve import resolve_item_agent_config
from workspace_app.config.schema import Settings
from workspace_app.factories import get_app_catalog
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.tooling.registry import CommandInfo, PackageInfo


def _pkg(name: str, *cmds: str) -> PackageInfo:
    return PackageInfo(
        name=name,
        commands=tuple(
            CommandInfo(name=c, description=f"{c}.", params_json_schema={}) for c in cmds
        ),
        install_dir=f"../.tools/{name}",
    )


PACKAGES = [_pkg("rca-tools", "spc", "pareto", "wafer-history"), _pkg("sci-plot", "chart")]
PKG_CMDS = {"spc", "pareto", "wafer-history", "chart"}


class _Recording(ScriptedAgentRunner):
    """Keeps every context it was asked to run, so a test can look at the one a
    door built."""

    def __init__(self) -> None:
        super().__init__([RunDone()])
        self.ctxs: list[Any] = []

    async def run(self, prompt, ctx):  # type: ignore[override]
        self.ctxs.append(ctx)
        async for ev in super().run(prompt, ctx):
            yield ev


def _build(monkeypatch, prefs: dict[str, bool], profile: str = "default"):
    """A real app with the two fake packages, one rca item carrying `prefs`, and
    the turn builder + workflow executor the app composed."""
    spec = make_spec()
    runner = _Recording()
    captured: dict[str, Any] = {}
    real_builder = app_mod.TurnContextBuilder
    real_executor = app_mod.WorkflowExecutor

    def _capture_builder(**kw):
        b = real_builder(**kw)
        captured["builder"] = b
        return b

    def _capture_executor(**kw):
        ex = real_executor(**kw)
        captured["executor"] = ex
        return ex

    monkeypatch.setattr(app_mod, "TurnContextBuilder", _capture_builder)
    monkeypatch.setattr(app_mod, "WorkflowExecutor", _capture_executor)
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=runner,
        packages=list(PACKAGES),
    )
    item_id = (
        spec.get_resource_manager(RcaInvestigation)
        .create(RcaInvestigation(title="t", owner="u", profile=profile, attached_tool_prefs=prefs))
        .resource_id
    )
    return spec, captured["builder"], captured["executor"], runner, item_id


async def _chat_ctx(spec, builder, item_id: str):
    """What `chat_send` hands the builder: the item's resolved config (entry
    level — resolve runs before the packages are known) and the rest."""
    return await builder.build_chat_turn(
        item_id,
        agent_config=resolve_item_agent_config(spec, get_app_catalog(Settings()), item_id),
        run_subagent=None,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="u",
        speaker=None,
    )


def _model_view(ctx) -> tuple[set[str], str]:
    """The package commands the runner would register for `ctx`, and the
    instructions it would send — through the runner's own agent assembly."""
    agent = _agent_for(
        ctx.agent_config,
        ctx.packages,
        ctx.unavailable_tools,
        app_slug=ctx.app_slug,
        template_profile=ctx.template_profile,
    )
    names = {t.name for t in agent.tools}
    instr = agent.instructions if isinstance(agent.instructions, str) else ""
    return names & PKG_CMDS, instr


# ─── the chat turn ──────────────────────────────────────────────────


async def test_a_chat_turn_holds_the_whole_package_minus_the_command_pinned_off(monkeypatch):
    """The app grants `rca-tools` whole; the item pinned `pareto` off. What the
    turn HOLDS is the other two commands, at command granularity, and the
    ceiling + pins it was computed from are spent."""
    spec, builder, _, _, iid = _build(monkeypatch, {"rca-tools:pareto": False})
    ctx = await _chat_ctx(spec, builder, iid)
    allowed = ctx.agent_config.allowed_tools or []
    assert "rca-tools:spc" in allowed and "rca-tools:wafer-history" in allowed
    assert "rca-tools:pareto" not in allowed and "rca-tools" not in allowed
    assert ctx.agent_config.tool_ceiling == [] and ctx.agent_config.tool_prefs == {}
    cmds, instr = _model_view(ctx)
    assert cmds == {"spc", "wafer-history", "chart"}
    assert "pareto" in instr  # #480: advertised as off, so the model can ask for it


async def test_a_legacy_whole_package_pin_still_governs_every_command_through_the_door(monkeypatch):
    """`{"rca-tools": false}` was written before commands were pickable; it goes
    on meaning the whole package, and a command key beside it wins for that
    command."""
    spec, builder, _, _, iid = _build(monkeypatch, {"rca-tools": False, "rca-tools:spc": True})
    ctx = await _chat_ctx(spec, builder, iid)
    cmds, _ = _model_view(ctx)
    assert cmds == {"spc", "chart"}


# ─── a workflow step: `tools:` is an intersection AFTER the pins ────


async def test_a_workflow_step_tools_list_is_not_widened_by_an_item_pin(monkeypatch):
    """The step said `tools: [read_file]`. A pin that turned `spc` ON for the
    item's chat must not add `spc` to the step — a step cannot widen its
    boundary, and neither can the picker widen it for the step."""
    _, _, executor, runner, iid = _build(monkeypatch, {"rca-tools:spc": True})
    await executor.drive_turn(iid, "no-such-chat", "u", "hello", ["read_file"])
    cmds, instr = _model_view(runner.ctxs[-1])
    assert cmds == set()
    # And nothing the step narrowed away is advertised as "off, ask the user":
    # the step's list is not the picker, and there is no user on a step.
    assert "## Tools available on request" not in instr


async def test_a_workflow_step_naming_a_package_gets_the_commands_the_item_holds_of_it(
    monkeypatch,
):
    """`tools: [rca-tools]` on an item that pinned `pareto` off: the step gets
    `spc` and `wafer-history`. The item's pins bind the step too — the
    picker's Off means off for every turn on the item."""
    _, _, executor, runner, iid = _build(monkeypatch, {"rca-tools:pareto": False})
    await executor.drive_turn(iid, "no-such-chat", "u", "hello", ["rca-tools", "read_file"])
    cmds, instr = _model_view(runner.ctxs[-1])
    assert cmds == {"spc", "wafer-history"}
    assert "pareto" in instr  # off by the item's preference: that IS the picker's business


async def test_a_workflow_step_with_an_empty_tools_list_holds_nothing_whatever_the_pins(
    monkeypatch,
):
    _, _, executor, runner, iid = _build(monkeypatch, {"rca-tools": True}, profile="tool-demo")
    await executor.drive_turn(iid, "no-such-chat", "u", "hello", [])
    cmds, _ = _model_view(runner.ctxs[-1])
    assert cmds == set()


# ─── the compaction summariser: "no tools, explicitly" ─────────────


async def test_the_compaction_summariser_holds_no_package_command_for_a_pinned_on_item(
    monkeypatch,
):
    spec, builder, _, runner, iid = _build(monkeypatch, {"rca-tools:spc": True})
    ctx = await _chat_ctx(spec, builder, iid)
    await AgentCompactor(runner).summarise(["m1"], ctx=ctx)
    cmds, instr = _model_view(runner.ctxs[-1])
    assert cmds == set()
    assert "## Tools available on request" not in instr


# ─── a sub-agent: its definition's list, bounded by what the parent holds ──


async def test_a_sub_agent_definition_naming_only_read_file_gets_no_package_command(monkeypatch):
    from workspace_app.api.subagent_run import _child_context
    from workspace_app.apps.subagents import SubagentDef

    spec, builder, _, _, iid = _build(monkeypatch, {"rca-tools:spc": True})
    parent = await _chat_ctx(spec, builder, iid)
    child = _child_context(
        parent, SubagentDef(name="r", description="d", body="b", tools=["read_file"])
    )
    cmds, _ = _model_view(child)
    assert cmds == set()


async def test_a_sub_agent_definition_naming_a_package_gets_what_the_parent_holds_of_it(
    monkeypatch,
):
    """A definition saved as `tools: [rca-tools]` resolves, at load, to the
    commands the parent turn holds — so a command the item pinned off is off
    for the sub-agent too, and the definition need not be rewritten when the
    package's commands change."""
    from workspace_app.apps.subagents import SubagentDef, clamp_tools

    spec, builder, _, _, iid = _build(monkeypatch, {"rca-tools:pareto": False})
    parent = await _chat_ctx(spec, builder, iid)
    defn = SubagentDef(name="r", description="d", body="b", tools=["rca-tools", "exec"])
    clamped = clamp_tools(defn, parent.agent_config.allowed_tools)
    assert clamped.tools == ["rca-tools:spc", "rca-tools:wafer-history", "exec"]
