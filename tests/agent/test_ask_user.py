"""`ask_user` — the agent asks the user a structured question (grill-me).

The whole point is that the *options* reach the UI as data, not prose: the
agent's question renders as buttons the user clicks, instead of a paragraph
they have to answer by typing. The options ride on the tool call's own
`tool_args`, which the turn reducer already persists and streams — so the tool
itself only has to describe the question well and then get out of the way.

It does NOT wait for an answer. The turn stops at this tool (the SDK's
`StopAtTools`), the user answers, and the next turn continues with the answer
in the transcript. Waiting would mean one pod blocking on an answer that can
arrive at any other pod — the cross-pod problem this design exists to avoid.
"""

from __future__ import annotations

import pytest
from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext
from workspace_app.agent.tools import ask_user_impl


def _ctx() -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(AgentToolContext())


async def test_one_question_with_options():
    out = await ask_user_impl(
        _ctx(),
        questions=[
            {
                "question": "Which storage backend?",
                "options": [
                    {"label": "Postgres", "description": "Durable, needs a server"},
                    {"label": "SQLite", "description": "Zero setup, single node"},
                ],
            }
        ],
    )

    assert "Which storage backend?" in out
    assert "Postgres" in out and "SQLite" in out


async def test_up_to_five_questions_are_accepted():
    """Independent decisions can be batched — that is what keeps a form-shaped
    ask from costing five turns."""
    out = await ask_user_impl(
        _ctx(),
        questions=[
            {
                "question": f"Q{i}",
                "options": [
                    {"label": "a", "description": ""},
                    {"label": "b", "description": ""},
                ],
            }
            for i in range(5)
        ],
    )

    assert all(f"Q{i}" in out for i in range(5))


async def test_more_than_five_is_refused():
    """A wall of questions is not a conversation. The cap is what keeps the
    agent from dumping its whole decision tree in one card."""
    out = await ask_user_impl(
        _ctx(),
        questions=[
            {
                "question": f"Q{i}",
                "options": [
                    {"label": "a", "description": ""},
                    {"label": "b", "description": ""},
                ],
            }
            for i in range(6)
        ],
    )

    assert out.startswith("error:")
    assert "5" in out


async def test_no_questions_is_refused():
    out = await ask_user_impl(_ctx(), questions=[])

    assert out.startswith("error:")


async def test_a_question_needs_at_least_two_options():
    """One option is not a choice — it is an announcement, and the user has
    nothing to decide. Two is the minimum that carries information."""
    out = await ask_user_impl(
        _ctx(),
        questions=[{"question": "Proceed?", "options": [{"label": "Yes", "description": ""}]}],
    )

    assert out.startswith("error:")


async def test_a_question_needs_a_question():
    out = await ask_user_impl(
        _ctx(),
        questions=[
            {
                "question": "   ",
                "options": [
                    {"label": "a", "description": ""},
                    {"label": "b", "description": ""},
                ],
            }
        ],
    )

    assert out.startswith("error:")


# ---------------------------------------------------------------------------
# The turn must stop here
# ---------------------------------------------------------------------------


def test_the_agent_stops_at_this_tool():
    """Without this the model keeps generating after asking — and, having no
    answer, invents one. `StopAtTools` is the SDK's own mechanism for it, so
    the guarantee is structural rather than a plea in the prompt.
    """
    from workspace_app.api.litellm_runner import ask_user_stop_behaviour

    behaviour = ask_user_stop_behaviour(["read_file", "ask_user"])

    # StopAtTools is a TypedDict — a plain dict at runtime, so this is an
    # ordinary key read once the "run_llm_again" branch is ruled out.
    assert behaviour != "run_llm_again"
    assert behaviour["stop_at_tool_names"] == ["ask_user"]


def test_a_turn_without_the_tool_runs_normally():
    """Every other turn must keep the default behaviour — a blanket stop would
    end turns after their first tool call."""
    from workspace_app.api.litellm_runner import ask_user_stop_behaviour

    assert ask_user_stop_behaviour(["read_file", "exec"]) == "run_llm_again"


@pytest.mark.parametrize("names", [None, []])
def test_no_tools_at_all_runs_normally(names):
    from workspace_app.api.litellm_runner import ask_user_stop_behaviour

    assert ask_user_stop_behaviour(names) == "run_llm_again"


def test_ask_user_has_a_strict_schema_that_names_the_option_fields():
    """The bug this pins: `ask_user` once took `list[dict[str, Any]]` and rode in
    the non-strict set, so the model was shown an open object with no field names
    to honour — and even a top model split between `label` and `option`, because
    the shape was the tool's to specify, not the model's to guess. The fix is a
    strict schema, so the field names are the API's contract, not a guess."""
    from agents import function_tool

    from workspace_app.agent.tools import _NONSTRICT_TOOLS, ask_user_impl

    assert "ask_user" not in _NONSTRICT_TOOLS  # it is a fixed shape, not an open one

    tool = function_tool(ask_user_impl, name_override="ask_user", strict_mode=True)
    assert tool.strict_json_schema is True
    option = tool.params_json_schema["$defs"]["AskOption"]
    # The option's fields are named and closed — the model cannot send `option`
    # instead of `label`, or any other stray key.
    assert set(option["properties"]) == {"label", "description"}
    assert set(option["required"]) == {"label", "description"}
    assert option["additionalProperties"] is False
