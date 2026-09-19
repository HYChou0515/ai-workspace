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
    filestore = SpecstarFileStore(spec)
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
        filestore=filestore,
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
    """Two shapes. Production's pre-turn compaction (`chat_send`) hands the
    summariser a bare context — the resolved config, no package list — so
    nothing can expand there and the pin cannot reach it; that shape is not a
    door and never was affected. A door-built context (a mid-turn compaction
    of a running turn's own ctx) carries the packages: the summariser's
    `allowed_tools=[]` must hold against a pin that is already spent."""
    from workspace_app.agent.context import AgentToolContext

    spec, builder, _, runner, iid = _build(monkeypatch, {"rca-tools:spc": True})
    bare = AgentToolContext(
        investigation_id=iid,
        agent_config=resolve_item_agent_config(spec, get_app_catalog(Settings()), iid),
    )
    await AgentCompactor(runner).summarise(["m1"], ctx=bare)
    assert _model_view(runner.ctxs[-1])[0] == set()

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


async def test_a_sub_agent_child_context_narrows_a_bare_package_to_what_the_parent_holds(
    monkeypatch,
):
    """The child's list is made in `_child_context`, whatever produced the
    definition — a file the loader clamped, or one `save_subagent` spliced into
    this very turn. Narrowing HERE is what makes every source equal: the splice
    handed `run_agent` an unclamped `tools: [rca-tools]` and the child got
    `pareto` back, pin or no pin."""
    from workspace_app.api.subagent_run import _child_context
    from workspace_app.apps.subagents import SubagentDef

    spec, builder, _, _, iid = _build(monkeypatch, {"rca-tools:pareto": False})
    parent = await _chat_ctx(spec, builder, iid)
    child = _child_context(
        parent, SubagentDef(name="r", description="d", body="b", tools=["rca-tools", "read_file"])
    )
    assert child.agent_config is not None
    assert child.agent_config.allowed_tools == [
        "rca-tools:spc",
        "rca-tools:wafer-history",
        "read_file",
    ]
    cmds, _ = _model_view(child)
    assert cmds == {"spc", "wafer-history"}


async def test_a_definition_saved_this_turn_is_delegated_with_the_narrowed_list(monkeypatch):
    """`save_subagent` splices the parsed definition into the turn's index so
    `run_agent` can use it before any re-read; the splice carries the SAME
    clamp the loader applies, so the index never holds a bare package."""
    from agents import RunContextWrapper

    from workspace_app.agent.tools import save_subagent_impl

    spec, builder, _, _, iid = _build(monkeypatch, {"rca-tools:pareto": False})
    parent = await _chat_ctx(spec, builder, iid)
    out = await save_subagent_impl(
        RunContextWrapper(parent), "digger", "d", ["rca-tools", "read_file"], "body"
    )
    assert "error" not in out
    [defn] = [d for d in parent.subagent_defs if d.name == "digger"]
    assert defn.tools == ["rca-tools:spc", "rca-tools:wafer-history", "read_file"]


async def test_the_turn_index_clamps_a_workspace_definition_to_the_finalized_grant(monkeypatch):
    """The pin on ORDER: `_finalized` runs before `_subagent_defs`. Hand the
    clamp the pre-finalize (entry-level) list and a definition saying
    `rca-tools` sails through verbatim — `rca-tools` IS in that list — and the
    child would hold `pareto`. Only a definition read through the builder can
    see that; a test that calls `clamp_tools` itself cannot."""
    spec, builder, _, _, iid = _build(monkeypatch, {"rca-tools:pareto": False})
    await builder._files.write(  # noqa: SLF001 — the workspace the turn reads
        iid,
        "/.agent/r/AGENT.md",
        b"---\nname: r\ndescription: d\ntools: [rca-tools, exec]\n---\nbody\n",
    )
    ctx = await _chat_ctx(spec, builder, iid)
    [defn] = [d for d in ctx.subagent_defs if d.name == "r"]
    assert defn.tools == ["rca-tools:spc", "rca-tools:wafer-history", "exec"]


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


# ─── a THIRD-PARTY package: the doors finalize against THIS turn's resolve ──


def _grant_wafer_history(monkeypatch):
    """The rca manifest with `wafer-history` declared AND in `tools[]`, for
    every reader of the manifest (resolve, the picker route, the turn builder)."""
    import importlib

    import msgspec

    from workspace_app.apps.manifest import load_app_manifest

    real = load_app_manifest("rca")
    fake = msgspec.structs.replace(
        real,
        agent=msgspec.structs.replace(
            real.agent,
            tools=[*real.agent.tools, "wafer-history"],
            external_tools={"wafer-history": "https://g/m"},
        ),
    )
    for mod in (
        "workspace_app.apps.catalog",
        "workspace_app.api.tools_routes",
        "workspace_app.api.turn_context",
    ):
        monkeypatch.setattr(importlib.import_module(mod), "load_app_manifest", lambda slug: fake)


def test_the_picker_door_finalizes_a_third_party_package_it_resolved(monkeypatch):
    """`[*pkgs, *external.packages]` — drop the external half and a granted,
    resolved third-party command shows `effective: False` unpinned, and its
    pin governs nothing."""
    from workspace_app.api import tools_routes

    from .conftest import register_rca_item
    from .test_tools_routes import _picker_with_packages, _rca_tools_pkg, _resolved

    _grant_wafer_history(monkeypatch)

    async def _fake(sandbox, locator, item_id):
        return _resolved()

    monkeypatch.setattr(tools_routes, "resolve_item_tools", _fake)
    spec, client, _ = _picker_with_packages(_rca_tools_pkg())

    on = register_rca_item(spec)
    rows = {r["key"]: r for r in client.get(f"/a/rca/items/{on}/tools").json()["tools"]}
    assert rows["wafer-history:trend"]["effective"] is True
    assert rows["wafer-history:trend"]["default_on"] is True

    off = register_rca_item(spec, attached_tool_prefs={"wafer-history:trend": False})
    rows = {r["key"]: r for r in client.get(f"/a/rca/items/{off}/tools").json()["tools"]}
    assert rows["wafer-history:trend"]["effective"] is False


async def test_the_turn_door_finalizes_a_third_party_package_it_resolved(monkeypatch):
    """The turn's package list is first-party PLUS this turn's third-party
    resolve. Drop the second half and `wafer-history` stays a bare entry,
    which `build_function_tools` expands to every command — the pin on
    `trend` governs nothing."""
    from workspace_app.api import turn_context

    from .test_tools_routes import _resolved

    _grant_wafer_history(monkeypatch)

    async def _fake(sandbox, locator, item_id):
        return _resolved()

    monkeypatch.setattr(turn_context, "resolve_item_tools", _fake)
    spec, builder, _, _, iid = _build(monkeypatch, {"wafer-history:trend": False})
    ctx = await _chat_ctx(spec, builder, iid)
    allowed = ctx.agent_config.allowed_tools or []
    assert "wafer-history" not in allowed  # expanded, not left as a bare entry
    assert "wafer-history:trend" not in allowed  # pinned off
    assert "wafer-history:trend" in ctx.agent_config.disabled_tools
    agent = _agent_for(ctx.agent_config, ctx.packages, ctx.unavailable_tools)
    assert "trend" not in {t.name for t in agent.tools}


async def test_a_node_naming_a_command_the_item_does_not_hold_says_so_in_the_log(
    monkeypatch, caplog
):
    """The validator refuses what it can see; a `pkg:cmd` it could not judge
    (an unresolved package, a zero-command one) or a command the item pinned
    off reaches the run, is dropped, and the run's log is where that is said."""
    import logging

    _, _, executor, runner, iid = _build(monkeypatch, {"rca-tools:pareto": False})
    with caplog.at_level(logging.WARNING, logger="workspace_app.api.turn_context"):
        await executor.drive_turn(
            iid, "no-such-chat", "u", "hello", ["rca-tools:typo", "rca-tools:pareto", "read_file"]
        )
    cmds, _ = _model_view(runner.ctxs[-1])
    assert cmds == set()
    [rec] = [r for r in caplog.records if "not held by this item" in r.getMessage()]
    assert "rca-tools:typo" in rec.getMessage() and "rca-tools:pareto" in rec.getMessage()
    assert "read_file" not in rec.getMessage()
