"""Which package commands this TURN has loaded the schema for
(`docs/plan-tool-on-demand-loading.md` P2).

The set lives on the turn's context and dies with it: a turn starts from the
catalog, and what it loaded does not carry into the next one. That is what
bounds the window — a conversation-level set would creep the whole tool
universe back in over a dozen turns, which is today's state arriving slowly.
"""

from __future__ import annotations

from workspace_app.agent.context import AgentToolContext


def test_a_turn_starts_from_the_catalog():
    assert AgentToolContext(investigation_id="i").loaded_tools == set()


def test_marking_one_loads_only_that_one():
    ctx = AgentToolContext(investigation_id="i")

    ctx.mark_tool_loaded("data-fetch")

    assert ctx.is_tool_loaded("data-fetch")
    assert not ctx.is_tool_loaded("plot-trend")


def test_marking_twice_is_the_same_as_once():
    # Two calls to one unloaded command inside a single step (a model may emit
    # several tool calls at once) must not be an error or a double entry.
    ctx = AgentToolContext(investigation_id="i")

    ctx.mark_tool_loaded("data-fetch")
    ctx.mark_tool_loaded("data-fetch")

    assert ctx.loaded_tools == {"data-fetch"}


def test_two_turns_do_not_share_the_set():
    # The classic mutable-default bug would make the catalog leak across every
    # turn in the process — and it would look like the feature working.
    first = AgentToolContext(investigation_id="i")
    second = AgentToolContext(investigation_id="i")

    first.mark_tool_loaded("data-fetch")

    assert second.loaded_tools == set()
