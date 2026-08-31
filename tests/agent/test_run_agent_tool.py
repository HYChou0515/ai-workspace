"""The `run_agent` tool — how the main agent delegates a sub-task.

It names one of the turn's sub-agent definitions and hands over a whole task;
what comes back is a report, not the work. The noisy middle stays in the
sub-agent's own context, which is the entire reason to delegate.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import (
    build_tools,
    delegation_is_available,
    run_agent_impl,
    save_subagent_impl,
)
from workspace_app.api.litellm_runner import _turn_instructions
from workspace_app.apps.subagents import SubagentDef, subagents_block
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources.agent_config import AgentConfig

_DIGGER = SubagentDef(name="log-digger", description="Digs logs", tools=["read_file"], body="dig")
_REPORTER = SubagentDef(name="reporter", description="Writes it up", tools=[], body="write")


def _ctx(defs=(_DIGGER, _REPORTER), calls=None, sink=None) -> RunContextWrapper[AgentToolContext]:
    async def run_agent(parent, defn, prompt, emit=None):
        if calls is not None:
            calls.append((defn.name, prompt))
        if emit is not None:
            emit(b"the sub-agent looked at app.log\n")
        return f"[{defn.name}] report"

    return RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            subagent_defs=defs,
            run_agent=run_agent,
            on_exec_output=sink,
        )
    )


async def test_delegating_returns_the_sub_agents_report():
    calls: list[tuple[str, str]] = []
    out = await run_agent_impl(_ctx(calls=calls), "log-digger", "find the first real error")
    assert calls == [("log-digger", "find the first real error")]
    assert out == "[log-digger] report"


async def test_an_unknown_name_comes_back_with_the_ones_that_exist():
    """The model picked from an index it was shown, so a bare rejection leaves it
    guessing at what it misread."""
    out = await run_agent_impl(_ctx(), "log_digger", "go")
    assert "log_digger" in out
    assert "log-digger" in out and "reporter" in out


async def test_the_sub_agents_work_streams_under_the_calling_card():
    """Delegating must not look like the turn froze — what the sub-agent is doing
    surfaces live on the tool card that started it."""
    seen: list[bytes] = []
    await run_agent_impl(_ctx(sink=seen.append), "log-digger", "go")
    assert b"looked at app.log" in b"".join(seen)


def test_the_tool_is_not_offered_when_there_is_nobody_to_delegate_to():
    """#537's lesson: granting a tool and then refusing every call reads to a
    model as "stop trying", and costs a round trip to learn it. An App that
    lists `run_agent` but ships no definitions simply doesn't get the tool."""
    assert "run_agent" not in [t.name for t in build_tools(["read_file", "run_agent"])]
    with_defs = build_tools(["read_file", "run_agent"], has_subagents=True)
    assert "run_agent" in [t.name for t in with_defs]


def test_the_turn_tells_the_model_who_it_can_delegate_to():
    """A sub-agent nobody knows about is never called. The names + when-to-use
    lines go into the system prompt the same way the skill index does."""
    note = subagents_block([_DIGGER, _REPORTER])
    assert "log-digger" in note and "Digs logs" in note
    assert "reporter" in note and "Writes it up" in note
    assert "run_agent" in note  # says HOW to call them, not just that they exist
    assert subagents_block([]) == ""


def _ctx_with(tools: list[str], defs=(_DIGGER,)) -> AgentToolContext:
    return AgentToolContext(
        investigation_id="inv-1",
        subagent_defs=defs,
        agent_config=AgentConfig(name="main", allowed_tools=tools),
    )


def test_the_delegation_note_reaches_the_system_prompt():
    note = _turn_instructions(_ctx_with(["read_file", "run_agent"]), None) or ""
    assert "log-digger" in note


def test_the_note_is_withheld_when_the_turn_has_no_tool_to_act_on_it():
    """The mirror of #537, and the half that was missing: advertising an index
    for a tool that was never built spends the model's next step on a call that
    cannot resolve. Reachable today — an App that lists neither `run_agent` nor
    `save_subagent` still loads `.agent/` files a user drops in the workspace."""
    note = _turn_instructions(_ctx_with(["read_file"]), None) or ""
    assert "log-digger" not in note
    assert "run_agent" not in note


def test_one_predicate_decides_both_the_tool_and_the_note():
    """`build_tools` and the prompt read the SAME function, so the two cannot
    drift into a prompt that names a tool the turn never got."""
    assert delegation_is_available(["run_agent"], True) is True
    assert delegation_is_available(["run_agent", "save_subagent"], False) is True
    assert delegation_is_available(["run_agent"], False) is False  # nothing to call
    assert delegation_is_available(["read_file"], True) is False  # App never listed it

    for allowed, has in (["run_agent"], True), (["read_file"], True), (["run_agent"], False):
        built = "run_agent" in [t.name for t in build_tools(allowed, has_subagents=has)]
        assert built is delegation_is_available(allowed, has)


async def test_a_sub_agent_saved_this_turn_is_callable_in_the_same_turn():
    """The delegation index is frozen when the turn starts, so one just written
    is not in it. Resolving falls back to reading `.agent/` live — the same thing
    `read_skill` has always done — or "create then use it" would need the user to
    send a second message for no reason they could see."""
    calls: list[tuple[str, str]] = []
    ctx = _ctx(defs=(), calls=calls)  # frozen index: empty, as at turn start
    ctx.context.files = WorkspaceFiles(MemoryFileStore())

    await save_subagent_impl(ctx, "line-finder", "Finds a line.", ["read_file"], "find it")
    out = await run_agent_impl(ctx, "line-finder", "which line says ERROR?")

    assert calls == [("line-finder", "which line says ERROR?")]
    assert out == "[line-finder] report"


def test_an_agent_that_can_create_a_sub_agent_is_offered_the_tool_to_call_one():
    """#537 says do not grant a tool that can only ever refuse. That is about a
    dead end — and this is not one: an agent holding `save_subagent` can make the
    list non-empty itself, in this same reply. Withholding `run_agent` until the
    next turn would be the dead end."""
    names = [t.name for t in build_tools(["read_file", "save_subagent", "run_agent"])]
    assert "run_agent" in names


def test_but_with_nothing_to_call_and_no_way_to_create_one_it_stays_unoffered():
    """The original rule, unchanged: no definitions and no `save_subagent` means
    every call would be refused, and a refusal reads to a model as "stop trying"."""
    names = [t.name for t in build_tools(["read_file", "run_agent"])]
    assert "run_agent" not in names


def test_a_switch_that_would_do_nothing_is_not_offered_to_the_user():
    """#480 offers OFF tools with "ask the user to turn this on". Turning
    `run_agent` on alone, with no definitions, leaves the picker showing it ON
    while `build_tools` still strips it — and it drops off this list too, so
    nothing anywhere explains the dead switch. Found by a regression review."""
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources import AgentConfig as Cfg

    off_with_nothing_to_call = _agent_for(
        Cfg(name="a", model="m", allowed_tools=["read_file"], disabled_tools=["run_agent", "exec"])
    )
    assert isinstance(off_with_nothing_to_call.instructions, str)
    assert "run_agent" not in off_with_nothing_to_call.instructions
    assert "exec" in off_with_nothing_to_call.instructions  # the section still works

    # ...but when it WOULD work once enabled, the offer is real and stays.
    worth_offering = _agent_for(
        Cfg(name="a", model="m", allowed_tools=["read_file"], disabled_tools=["run_agent"]),
        has_subagents=True,
    )
    assert isinstance(worth_offering.instructions, str)
    assert "run_agent" in worth_offering.instructions


async def test_with_nothing_defined_the_refusal_points_at_how_to_make_one():
    """The tool is only granted here because `save_subagent` is held, so the
    refusal has to hand the model its next move — otherwise granting it was the
    dead end #537 warns about."""
    ctx = _ctx(defs=())
    ctx.context.agent_config = AgentConfig(
        name="main", allowed_tools=["run_agent", "save_subagent"]
    )
    out = await run_agent_impl(ctx, "log-digger", "go")
    assert "save_subagent" in out


async def test_without_that_tool_the_refusal_just_says_there_are_none():
    """...and it must not advertise a tool this turn does not hold."""
    ctx = _ctx(defs=())
    ctx.context.agent_config = AgentConfig(name="main", allowed_tools=["run_agent"])
    out = await run_agent_impl(ctx, "log-digger", "go")
    assert "save_subagent" not in out
    assert "none are defined" in out


async def test_no_seam_on_this_turn_is_reported_not_raised():
    ctx = RunContextWrapper(AgentToolContext(investigation_id="inv-1"))
    assert "error" in await run_agent_impl(ctx, "log-digger", "go")
