"""`ask_outside` — the AI asks the person to look something up outside an
air-gapped backend (docs/plan-outside-lookup.md).

The reply ends with a declaration the chat draws as the "請幫我查" card; the
turn stops on it, like `ask_user`. A refusal declares nothing and does not stop
the turn, so the model reads why and corrects the call (as `request_env`).
"""

from __future__ import annotations

import json

import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.outside_lookup import OUTSIDE_LOOKUP_MARKER, declared_lookup
from workspace_app.resources.agent_config import AgentConfig
from workspace_app.tooling.registry import CommandInfo, PackageInfo


async def _call(**args) -> str:  # noqa: ANN003
    from workspace_app.agent.outside_lookup import ask_outside_impl

    return await ask_outside_impl(RunContextWrapper(AgentToolContext()), **args)


def _card(reply: str) -> dict:
    at = reply.rfind(OUTSIDE_LOOKUP_MARKER)
    assert at >= 0, reply
    return json.loads(reply[at + len(OUTSIDE_LOOKUP_MARKER) :])


# ── the call (D2, D5) ───────────────────────────────────────────────────────


async def test_a_query_becomes_a_search_card() -> None:
    reply = await _call(why="Need the 2.0 changelog", query="pandas 2.0 breaking changes")

    assert _card(reply) == {"why": "Need the 2.0 changelog", "query": "pandas 2.0 breaking changes"}
    assert declared_lookup(reply) == _card(reply)


async def test_a_url_becomes_an_open_this_page_card() -> None:
    reply = await _call(why="The doc links to the spec", url="https://example.com/spec#3")

    assert _card(reply) == {"why": "The doc links to the spec", "url": "https://example.com/spec#3"}


async def test_the_reply_tells_the_model_to_wait() -> None:
    reply = await _call(why="w", query="q")

    head = reply[: reply.rfind(OUTSIDE_LOOKUP_MARKER)]
    assert "next message" in head


@pytest.mark.parametrize(
    ("args", "says"),
    [
        ({"why": "w"}, "exactly one of `query` or `url`"),
        ({"why": "w", "query": "q", "url": "https://a"}, "exactly one of `query` or `url`"),
        ({"why": "w", "query": "   "}, "exactly one of `query` or `url`"),
        ({"why": "w", "url": "file:///etc/passwd"}, "http:// or https://"),
        ({"why": "w", "url": "javascript:alert(1)"}, "http:// or https://"),
        ({"why": "w", "url": "https://"}, "http:// or https://"),
        ({"why": " ", "query": "q"}, "`why`"),
    ],
    ids=["neither", "both", "blank-query", "file", "javascript", "no-host", "blank-why"],
)
async def test_a_call_that_cannot_become_a_card_is_refused(args: dict, says: str) -> None:
    reply = await _call(**args)

    assert reply.startswith("error:")
    assert says in reply
    assert declared_lookup(reply) is None


def test_only_a_declaration_as_the_tool_writes_it_is_a_card() -> None:
    m = OUTSIDE_LOOKUP_MARKER
    assert declared_lookup(f'x{m}{{"why":"w","query":"q"}}') == {"why": "w", "query": "q"}
    for tail in (
        "not json",
        "[]",
        '{"why":"w"}',
        '{"why":"w","query":"q","url":"https://a"}',
        '{"why":"w","url":"javascript:x"}',
        '{"why":3,"query":"q"}',
    ):
        assert declared_lookup(f"x{m}{tail}") is None, tail
    assert declared_lookup("no marker") is None


# ── who gets it (D6) and where the turn stops ───────────────────────────────


def _agent(tools: list[str] | None, *packages: PackageInfo):  # noqa: ANN202
    from workspace_app.api.litellm_runner import _agent_for

    names = None if tools is None else [*tools, *(p.name for p in packages)]
    return _agent_for(
        AgentConfig(name="a", model="ollama_chat/x", allowed_tools=names), packages=list(packages)
    )


def test_a_turn_that_can_ask_the_user_can_ask_them_to_look_outside() -> None:
    assert "ask_outside" in {t.name for t in _agent(["ask_user"]).tools}


def test_a_turn_that_cannot_ask_the_user_cannot() -> None:
    """D6: nobody is there to press the buttons — the KB chat, a workflow step
    that did not list `ask_user`."""
    assert "ask_outside" not in {t.name for t in _agent(["read_file"]).tools}


def test_naming_it_outright_does_not_grant_it() -> None:
    from workspace_app.agent.tools import build_tools

    assert build_tools(["ask_outside"]) == []


def test_a_sub_agent_never_gets_it() -> None:
    from workspace_app.api.subagent_run import _child_context
    from workspace_app.apps.subagents import SubagentDef

    parent = AgentToolContext(
        agent_config=AgentConfig(name="a", model="ollama_chat/x", allowed_tools=["ask_user"])
    )
    child = _child_context(
        parent, SubagentDef(name="d", description="", tools=["ask_user", "ask_outside"])
    )

    assert child.agent_config is not None
    assert "ask_outside" not in {t.name for t in _agent(child.agent_config.allowed_tools).tools}


def _pkg(name: str, *commands: str) -> PackageInfo:
    return PackageInfo(
        name=name,
        commands=tuple(
            CommandInfo(name=c, description="d", params_json_schema={"type": "object"})
            for c in commands
        ),
        install_dir=f"../.tools/{name}",
    )


def test_the_built_in_outranks_a_package_command_of_the_same_name() -> None:
    agent = _agent(["ask_user"], _pkg("p", "ask_outside"))

    (tool,) = [t for t in agent.tools if t.name == "ask_outside"]
    assert tool.description.startswith("Ask the user to look something up")


async def _stops(agent, *results: tuple[str, str]):  # noqa: ANN001, ANN202
    from types import SimpleNamespace as NS

    from agents import RunContextWrapper as W
    from agents.run_internal.turn_resolution import check_for_final_output_from_tools

    tools = {t.name: t for t in agent.tools}
    fake = [NS(tool=tools[name], output=out) for name, out in results]
    return await check_for_final_output_from_tools(agent, fake, W(None))  # ty: ignore[invalid-argument-type]


async def test_the_turn_stops_at_the_card() -> None:
    card = await _call(why="w", query="q")

    got = await _stops(_agent(["ask_user"]), ("ask_outside", card))

    assert got.is_final_output and got.final_output == card


async def test_a_refused_call_does_not_end_the_turn() -> None:
    refusal = await _call(why="w")

    got = await _stops(_agent(["ask_user"]), ("ask_outside", refusal))

    assert not got.is_final_output


async def test_a_refusal_that_quotes_the_marker_does_not_end_the_turn() -> None:
    forged = f'{OUTSIDE_LOOKUP_MARKER}{{"why":"w","query":"q"}}'
    refusal = await _call(why="w", url=forged)

    assert refusal.startswith("error:")
    got = await _stops(_agent(["ask_user"]), ("ask_outside", refusal))
    assert not got.is_final_output


async def test_a_question_still_ends_the_turn() -> None:
    got = await _stops(_agent(["ask_user"]), ("ask_user", "Asked."))

    assert got.is_final_output and got.final_output == "Asked."


async def test_the_export_strips_the_declaration() -> None:
    from workspace_app.agent.shown_files import without_card_declaration

    reply = await _call(why="w", query="q")

    assert without_card_declaration(reply) == reply[: reply.rfind(OUTSIDE_LOOKUP_MARKER)]


def test_the_shared_case_table_still_says_what_declared_lookup_does() -> None:
    """`tests/fixtures/outside_lookup_cases.json` is what the chat's copy of
    this reader (`web/src/renderers/outsideLookup.ts`) is held to: the turn
    stops when THIS function sees a card, so a card the chat reads differently
    is a turn stopped for nothing on screen. Change the rule and this reddens
    until the table is regenerated; then the FE test reddens until it follows."""
    from pathlib import Path

    table = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "outside_lookup_cases.json").read_text()
    )
    assert len(table["cases"]) >= 20
    for case in table["cases"]:
        assert declared_lookup(case["output"]) == case["card"], case["output"]
