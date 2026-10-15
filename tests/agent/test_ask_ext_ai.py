"""`ask_ext_ai` — the AI asks the company's web AI, through the person's helper
(docs/plan-ext-ai.md).

The reply ends with the same declaration `ask_outside` uses, as a third kind of
card: `{why, question}`. The turn stops on it; a refusal declares nothing and
does not stop it.
"""

from __future__ import annotations

import json

import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.outside_lookup import OUTSIDE_LOOKUP_MARKER, declared_lookup
from workspace_app.resources.agent_config import AgentConfig


async def _call(**args) -> str:  # noqa: ANN003
    from workspace_app.agent.ext_ai import ask_ext_ai_impl

    return await ask_ext_ai_impl(RunContextWrapper(AgentToolContext()), **args)


def _card(reply: str) -> dict:
    at = reply.rfind(OUTSIDE_LOOKUP_MARKER)
    assert at >= 0, reply
    return json.loads(reply[at + len(OUTSIDE_LOOKUP_MARKER) :])


# ── the call (D1, D2) ───────────────────────────────────────────────────────


async def test_a_question_becomes_an_ask_the_external_ai_card() -> None:
    reply = await _call(why="Compare the two fixes", question="Which is safer, A or B?\nA: …\nB: …")

    card = {"why": "Compare the two fixes", "question": "Which is safer, A or B?\nA: …\nB: …"}
    assert _card(reply) == card
    assert declared_lookup(reply) == card


async def test_the_reply_tells_the_model_to_wait() -> None:
    reply = await _call(why="w", question="q")

    head = reply[: reply.rfind(OUTSIDE_LOOKUP_MARKER)]
    assert "next message" in head


@pytest.mark.parametrize(
    ("args", "says"),
    [
        ({"why": "", "question": "q"}, "`why`"),
        ({"why": "w", "question": "  "}, "`question`"),
    ],
)
async def test_a_call_that_cannot_become_a_card_is_refused(args: dict, says: str) -> None:
    reply = await _call(**args)

    assert reply.startswith("error:") and says in reply
    assert declared_lookup(reply) is None


def test_the_tool_says_what_it_is_for_and_what_ask_outside_is_for() -> None:
    """D6: the original page comes from `ask_outside`; an analysis from here.
    Said in THIS description only: a turn holding it always holds
    `ask_outside` (`ask_ext_ai_granted`), but not the other way round, and a
    description naming a tool the turn lacks sends the model looking for it."""
    from workspace_app.agent.ext_ai import ask_ext_ai_impl
    from workspace_app.agent.outside_lookup import ask_outside_impl

    mine, theirs = ask_ext_ai_impl.__doc__ or "", ask_outside_impl.__doc__ or ""
    assert "ask_outside" in mine and "not an original source" in mine
    assert "ask_ext_ai" not in theirs
    # text only (D3); what is sent is what the user leaves in the box (D2)
    assert "text" in mine and "the user may edit" in mine


def test_only_a_declaration_as_the_tool_writes_it_is_a_card() -> None:
    m = OUTSIDE_LOOKUP_MARKER
    assert declared_lookup(f'x{m}{{"why":"w","question":"q"}}') == {"why": "w", "question": "q"}
    for tail in (
        '{"why":"w","question":"q","query":"k"}',
        '{"why":"w","question":""}',
        '{"why":"w","question":3}',
    ):
        assert declared_lookup(f"x{m}{tail}") is None, tail


# ── who gets it (D5) and where the turn stops ───────────────────────────────


def _agent(tools: list[str] | None, *, in_chat: bool = True, ext_ai: bool = True):  # noqa: ANN202
    from workspace_app.api.litellm_runner import _agent_for

    return _agent_for(
        AgentConfig(name="a", model="ollama_chat/x", allowed_tools=tools),
        in_chat=in_chat,
        ext_ai=ext_ai,
    )


def test_a_chat_turn_that_can_ask_the_user_gets_it_when_the_deploy_turns_it_on() -> None:
    assert "ask_ext_ai" in {t.name for t in _agent(["ask_user"]).tools}


def test_a_deploy_that_did_not_turn_it_on_has_no_such_tool() -> None:
    assert "ask_ext_ai" not in {t.name for t in _agent(["ask_user"], ext_ai=False).tools}


def test_a_turn_outside_a_chat_cannot() -> None:
    assert "ask_ext_ai" not in {t.name for t in _agent(["ask_user"], in_chat=False).tools}


def test_a_turn_that_cannot_ask_the_user_cannot() -> None:
    assert "ask_ext_ai" not in {t.name for t in _agent(["read_file"]).tools}


def test_naming_it_outright_does_not_grant_it() -> None:
    from workspace_app.agent.tools import build_tools

    assert build_tools(["ask_ext_ai"]) == []


def test_a_sub_agent_never_gets_it() -> None:
    from workspace_app.apps.subagents import SUBAGENT_FORBIDDEN_TOOLS

    assert "ask_ext_ai" in SUBAGENT_FORBIDDEN_TOOLS


async def _stops(agent, *results: tuple[str, str]):  # noqa: ANN001, ANN202
    from types import SimpleNamespace as NS

    from agents import RunContextWrapper as W
    from agents.run_internal.turn_resolution import check_for_final_output_from_tools

    tools = {t.name: t for t in agent.tools}
    fake = [NS(tool=tools[name], output=out) for name, out in results]
    return await check_for_final_output_from_tools(agent, fake, W(None))  # ty: ignore[invalid-argument-type]


async def test_the_turn_stops_at_the_card() -> None:
    card = await _call(why="w", question="q")

    got = await _stops(_agent(["ask_user"]), ("ask_ext_ai", card))

    assert got.is_final_output and got.final_output == card


async def test_a_refused_call_does_not_end_the_turn() -> None:
    refusal = await _call(why="", question="q")

    got = await _stops(_agent(["ask_user"]), ("ask_ext_ai", refusal))

    assert not got.is_final_output
