"""The `run_agent` tool — how the main agent delegates a sub-task.

It names one of the turn's sub-agent definitions and hands over a whole task;
what comes back is a report, not the work. The noisy middle stays in the
sub-agent's own context, which is the entire reason to delegate.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import build_tools, run_agent_impl, save_subagent_impl
from workspace_app.api.litellm_runner import _turn_instructions
from workspace_app.apps.subagents import SubagentDef, subagents_block
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore

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


def test_the_delegation_note_reaches_the_system_prompt():
    ctx = AgentToolContext(investigation_id="inv-1", subagent_defs=(_DIGGER,))
    assert "log-digger" in (_turn_instructions(ctx, None) or "")


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
