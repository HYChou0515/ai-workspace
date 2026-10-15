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
from workspace_app.api.litellm_runner import _agent_for, _map_event, enabled_function_tools
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
    assert sent["data-fetch"].description == "Fetch a lot's history. [rca-tools]"
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
    # …and it records THIS event as the one the reducer must not store, taking
    # the record as it goes: one record answers one call, so a reused call id
    # cannot silence a later real message (verify round F2).
    assert ctx.ephemeral_outputs == ["c1"]
    assert "c1" not in ctx.catalog_calls


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


# ── the sync readers that are not the SDK's run loop ────────────────────


async def test_the_sync_reader_agrees_with_the_sdk_in_every_state():
    """`enabled_function_tools` exists because `replay` and the trace line run
    with no event loop and cannot await `get_all_tools`. It mirrors one rule of
    the SDK's, so the SDK is the oracle: a parity test, not two implementations
    kept alike by hand (every state that matters, both answers compared)."""
    agent = _agent()
    fresh = AgentToolContext(investigation_id="i")
    partly = AgentToolContext(investigation_id="i")
    partly.mark_tool_loaded("data-fetch")
    allloaded = AgentToolContext(investigation_id="i")
    allloaded.mark_tool_loaded("data-fetch")
    allloaded.mark_tool_loaded("plot-trend")

    for ctx in (fresh, partly, allloaded):
        from agents import RunContextWrapper

        oracle = [
            t.name for t in await agent.get_all_tools(RunContextWrapper(ctx)) if hasattr(t, "name")
        ]
        assert [t.name for t in enabled_function_tools(agent, ctx)] == oracle, ctx.loaded_tools


async def test_the_sync_reader_never_answers_with_a_name_twice():
    """What the raw registry does, and why neither caller may read it: the
    payload would carry two definitions of one name, which `dedupe_tools`'
    docstring calls a payload the provider rejects wholesale."""
    agent = _agent()
    registered = [t.name for t in agent.tools]
    assert registered.count("data-fetch") == 2  # the twins ARE both registered

    sent = [t.name for t in enabled_function_tools(agent, AgentToolContext(investigation_id="i"))]

    assert len(sent) == len(set(sent)), sent


async def test_load_tool_is_not_granted_to_a_turn_that_holds_no_package_command():
    """`load_tool` was gated on the ITEM having a bundle installed, not on this
    turn being granted any command — so every sub-agent with a narrowed
    `allowed_tools` (every `ask_knowledge_base` child, every workflow node with
    its own `tools:`) got one whose only possible answer was "there is no tool
    called that. Tools you can load: ." (round 1 A6).
    """
    agent = _agent_for(
        AgentConfig(name="ws", system_prompt="anchor", allowed_tools=["read_file"]),
        packages=_packages(),
    )
    sent = await _sent(agent, AgentToolContext(investigation_id="i"))

    assert "read_file" in sent
    assert "load_tool" not in sent
    assert "data-fetch" not in sent


async def test_load_tool_is_granted_when_one_command_is():
    """The control: the gate is the granted commands, so granting one brings it
    back. A mutation that drops `load_tool` wholesale reddens here."""
    agent = _agent_for(
        AgentConfig(
            name="ws", system_prompt="anchor", allowed_tools=["read_file", "rca-tools:data-fetch"]
        ),
        packages=_packages(),
    )
    sent = await _sent(agent, AgentToolContext(investigation_id="i"))

    assert "load_tool" in sent
    assert "data-fetch" in sent
    assert "plot-trend" not in sent  # …and only the command that was granted


async def test_one_call_loading_a_bundle_and_one_of_its_commands_says_each_name_once():
    """The names go into a sentence a PERSON reads (the chat card and, before
    round 1 A1, the stored message). `load_tool(["rca-tools", "data-fetch"])`
    resolves `data-fetch` twice, and it read
    `載入 \u0060data-fetch\u0060、\u0060plot-trend\u0060、\u0060data-fetch\u0060 的用法`
    (round 1 B1)."""
    agent = _agent()
    ctx = AgentToolContext(investigation_id="i")
    load = (await _sent(agent, ctx))["load_tool"]

    await load.on_invoke_tool(
        _tool_ctx(ctx, "load_tool", call_id="c1"), '{"names": ["rca-tools", "data-fetch"]}'
    )

    assert ctx.catalog_calls["c1"] == ["data-fetch", "plot-trend"]
    event = _map_event(_output_event("c1", "…schema…"), ctx)
    assert isinstance(event, ToolEnd)
    assert event.output.count("data-fetch") == 1


async def test_the_trace_line_lists_what_was_sent_not_what_is_registered(monkeypatch):
    """`_emit_llm_trace` builds the one line an operator reads to see what went
    out. It read `agent.tools`, so it listed every package command twice as
    soon as the catalog registered twins (round 1 A12) — and nothing pinned it.
    """
    from workspace_app.api import litellm_runner as lr

    seen: list[str] = []
    monkeypatch.setattr(lr, "build_trace", _capture_tools(seen))

    cfg = AgentConfig(name="ws", system_prompt="anchor")
    ctx = AgentToolContext(investigation_id="i", packages=_packages(), agent_config=cfg)
    lr._emit_llm_trace(
        _agent_for(cfg, packages=_packages()),
        ctx,
        runner_base_url=None,
        tool_calls=0,
        content_text="",
    )

    assert seen, "the trace was not built"
    assert seen.count("data-fetch") == 1, seen
    assert len(seen) == len(set(seen)), seen


def _capture_tools(sink: list[str]):
    from workspace_app.api.llm_trace import build_trace as real

    def fake(**kw):
        sink.extend(kw.get("tools") or [])
        return real(**kw)

    return fake
