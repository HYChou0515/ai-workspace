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
from workspace_app.api.events import ToolEnd
from workspace_app.api.litellm_runner import _agent_for, _map_event
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


def _tool_ctx(ctx: AgentToolContext, name: str, *, call_id: str = "id"):
    from agents.tool_context import ToolContext
    from agents.usage import Usage

    return ToolContext(
        context=ctx, tool_name=name, tool_call_id=call_id, tool_arguments="{}", usage=Usage()
    )


# ── what reaches the chat and the stored thread ──────────────────────────


def _output_event(call_id: str, text: str):
    """The `tool_output` stream event shape `_map_event` reads."""
    from dataclasses import dataclass

    @dataclass
    class _Raw:
        call_id: str

    @dataclass
    class _Item:
        raw_item: object
        output: object

    @dataclass
    class _Ev:
        type: str
        name: str
        item: object

    return _Ev(
        type="run_item_stream_event",
        name="tool_output",
        item=_Item(raw_item=_Raw(call_id=call_id), output=text),
    )


async def test_a_catalog_hit_does_not_put_the_schema_in_the_thread():
    """The schema is the fattest thing in this design. The model needs it for
    the next step — the SDK's own run history carries it — but the chat card
    and the STORED message must not, or every turn that loaded a tool drags its
    schema into the next turn's window and the saving is undone over a few
    turns (`docs/plan-tool-on-demand-loading.md`)."""
    agent = _agent()
    ctx = AgentToolContext(investigation_id="i")
    fetch = (await _sent(agent, ctx))["data-fetch"]
    reply = await fetch.on_invoke_tool(_tool_ctx(ctx, "data-fetch", call_id="c1"), "{}")
    assert '"lot"' in reply  # the model did get the schema

    event = _map_event(_output_event("c1", reply), ctx)

    assert isinstance(event, ToolEnd)
    assert "lot" not in event.output
    assert "data-fetch" in event.output


async def test_load_tool_names_what_it_loaded_instead_of_dumping_schemas():
    agent = _agent()
    ctx = AgentToolContext(investigation_id="i")
    load = (await _sent(agent, ctx))["load_tool"]
    reply = await load.on_invoke_tool(
        _tool_ctx(ctx, "load_tool", call_id="c2"), '{"names": ["rca-tools"]}'
    )

    event = _map_event(_output_event("c2", reply), ctx)

    assert isinstance(event, ToolEnd)
    assert "data-fetch" in event.output and "plot-trend" in event.output
    assert "properties" not in event.output


async def test_a_real_call_of_an_already_loaded_tool_keeps_its_output():
    """The control. It is the SAME turn, with `data-fetch` already loaded —
    so "this turn loaded something" is true and only the CALL ID separates
    the catalog's answer from the tool's own.

    A weaker control (a turn that loaded nothing) passed a mutation keyed on
    `loaded_tools` instead of the call id, which is the whole point of the
    key: a loaded command and its catalog entry share a name.
    """
    agent = _agent()
    ctx = AgentToolContext(investigation_id="i")
    fetch = (await _sent(agent, ctx))["data-fetch"]
    await fetch.on_invoke_tool(_tool_ctx(ctx, "data-fetch", call_id="c1"), "{}")
    assert ctx.loaded_tools == {"data-fetch"}

    # The real tool now runs, under a new call id.
    event = _map_event(_output_event("c9", "exit=0\nlot A42 shipped"), ctx)

    assert isinstance(event, ToolEnd)
    assert event.output == "exit=0\nlot A42 shipped"


async def test_the_streamed_turn_passes_its_context_to_the_mapper(monkeypatch):
    """Through the REAL loop, not the mapper alone.

    `_map_event` taking the turn is useless unless the production call site
    hands it over, and a unit test that calls the mapper directly cannot tell:
    dropping `ctx` at the streaming call site left every test green.
    """
    from tests.api.test_litellm_runner import _ctx
    from workspace_app.api import litellm_runner as lr

    ctx = _ctx()
    ctx.mark_tool_loaded("data-fetch", call_id="c1")
    events = [_output_event("c1", '{"properties": {"lot": {"type": "string"}}}')]
    monkeypatch.setattr(lr.Runner, "run_streamed", lambda *a, **k: _FakeStreamed(events))

    out = [ev async for ev in lr.LitellmAgentRunner().run("p", ctx) if isinstance(ev, ToolEnd)]

    assert [e.output for e in out] == ["載入 `data-fetch` 的用法"]


class _FakeStreamed:
    """The same shape `tests/api/test_reply_provenance.py` drives the loop with."""

    def __init__(self, events):
        self._events = events
        self.context_wrapper = type("C", (), {"usage": type("U", (), {})()})()

    async def stream_events(self):
        for ev in self._events:
            yield ev
