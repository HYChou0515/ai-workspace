"""The catalog reaches the model, and turns into real schemas as it is used
(`docs/plan-tool-on-demand-loading.md` P3).

Each granted package command is registered TWICE under one name: the catalog
entry (one line, no schema) and the executable tool. Exactly one of the pair is
enabled per step, so what the provider receives is one entry per name — the
catalog before the turn reaches for it, the real schema after.

The filtering is the SDK's own `is_enabled`, evaluated inside the run loop
(`agents/run.py:1028` → `agents/agent.py:254`), so these tests ask the SDK for
the enabled set rather than re-implementing the rule.
"""

from __future__ import annotations

from typing import Any

from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.litellm_runner import _agent_for
from workspace_app.resources import AgentConfig
from workspace_app.tooling.registry import CommandInfo, PackageInfo


def _packages() -> list[PackageInfo]:
    return [
        PackageInfo(
            name="rca-tools",
            install_dir="../.tools/rca-tools",
            commands=(
                CommandInfo(
                    "data-fetch",
                    "Fetch a lot's history. Takes a lot id and a date range.",
                    {"type": "object", "properties": {"lot": {"type": "string"}}},
                ),
                CommandInfo("plot-trend", "Plot a trend.", {"type": "object", "properties": {}}),
            ),
        )
    ]


def _agent(**kw: Any):
    cfg = AgentConfig(name="ws", system_prompt="anchor", **kw)
    return _agent_for(cfg, packages=_packages())


async def _sent(agent, ctx: AgentToolContext) -> dict[str, Any]:
    """What this step would actually send, by name — the SDK's own answer."""
    tools = await agent.get_all_tools(RunContextWrapper(ctx))
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), f"a name reached the provider twice: {names}"
    return {t.name: t for t in tools}


async def test_a_fresh_turn_is_sent_the_catalog_not_the_schemas():
    agent = _agent()
    sent = await _sent(agent, AgentToolContext(investigation_id="i"))

    assert "data-fetch" in sent
    # One line of what it is, and no parameters: the expensive half is absent.
    assert sent["data-fetch"].description == "Fetch a lot's history."
    assert sent["data-fetch"].params_json_schema["properties"] == {}
    # …and the way to ask for the usage up front is there.
    assert "load_tool" in sent


async def test_loading_one_command_swaps_only_that_one_for_its_real_schema():
    agent = _agent()
    ctx = AgentToolContext(investigation_id="i")

    ctx.mark_tool_loaded("data-fetch")
    sent = await _sent(agent, ctx)

    assert sent["data-fetch"].params_json_schema["properties"] == {"lot": {"type": "string"}}
    # The full description, not the catalog line.
    assert "rca-tools" in sent["data-fetch"].description
    # Its neighbour is untouched — loading is per command, not per bundle.
    assert sent["plot-trend"].params_json_schema["properties"] == {}


async def test_both_halves_are_registered_so_the_swap_needs_no_rebuild():
    """The pair lives on the agent; only `is_enabled` changes between steps.
    Mutating the agent's tool list mid-turn would be the alternative, and the
    SDK re-reads `agent.tools` per step precisely so nothing has to."""
    agent = _agent()

    registered = [t.name for t in agent.tools]

    assert registered.count("data-fetch") == 2
    assert registered.count("plot-trend") == 2


async def test_a_command_a_builtin_shadows_gets_no_catalog_entry():
    """`dedupe_tools` drops the executable half of a command whose name a
    built-in already holds (first-wins). An entry for it would advertise
    something that can never run."""
    packages = [
        PackageInfo(
            name="rca-tools",
            install_dir="../.tools/rca-tools",
            commands=(CommandInfo("exec", "Shadowed by the built-in.", {"type": "object"}),),
        )
    ]
    agent = _agent_for(AgentConfig(name="ws", system_prompt="anchor"), packages=packages)
    sent = await _sent(agent, AgentToolContext(investigation_id="i"))

    assert "exec" in sent
    # The built-in's own schema, not a catalog stub.
    assert sent["exec"].params_json_schema["properties"] != {}
    assert [t.name for t in agent.tools].count("exec") == 1


async def test_calling_an_unloaded_command_loads_it_and_asks_for_a_retry():
    agent = _agent()
    ctx = AgentToolContext(investigation_id="i")
    sent = await _sent(agent, ctx)

    reply = await sent["data-fetch"].on_invoke_tool(
        _tool_ctx(ctx, "data-fetch"), '{"lot": "guessed"}'
    )

    assert ctx.is_tool_loaded("data-fetch")
    assert "again" in reply
    assert '"lot"' in reply  # the real schema came back
    # …and the next step sends the executable half.
    assert (await _sent(agent, ctx))["data-fetch"].params_json_schema["properties"] != {}


async def test_load_tool_takes_a_command_name_and_a_bundle_name():
    agent = _agent()
    ctx = AgentToolContext(investigation_id="i")
    load = (await _sent(agent, ctx))["load_tool"]

    await load.on_invoke_tool(_tool_ctx(ctx, "load_tool"), '{"names": ["data-fetch"]}')
    assert ctx.loaded_tools == {"data-fetch"}

    await load.on_invoke_tool(_tool_ctx(ctx, "load_tool"), '{"names": ["rca-tools"]}')
    assert ctx.loaded_tools == {"data-fetch", "plot-trend"}


async def test_load_tool_says_what_is_there_when_the_name_is_wrong():
    """Silence reads as "that tool is gone" and the model stops looking for the
    one it actually wanted."""
    agent = _agent()
    ctx = AgentToolContext(investigation_id="i")
    load = (await _sent(agent, ctx))["load_tool"]

    reply = await load.on_invoke_tool(_tool_ctx(ctx, "load_tool"), '{"names": ["nope"]}')

    assert "nope" in reply
    assert "data-fetch" in reply and "plot-trend" in reply
    assert ctx.loaded_tools == set()


def _tool_ctx(ctx: AgentToolContext, name: str):
    from agents.tool_context import ToolContext
    from agents.usage import Usage

    return ToolContext(
        context=ctx, tool_name=name, tool_call_id="id", tool_arguments="{}", usage=Usage()
    )
