"""P6 of `docs/plan-wui-publish-and-agent-hub.md`: the agent panel's server half.

The panel shows what others will get before it is published (A6): a summary
asked of the code a conversation runs through, and a TEST chat that runs as
the agent in the author's own workspace — the same instructions, the same
narrowed tools and the same skills a conversation copy has. "The same" is
measured against a copy, the oracle, never kept alike by hand.
"""

from __future__ import annotations

import re

from agents import RunContextWrapper

from workspace_app.agent.tools import read_skill_impl
from workspace_app.api import create_app
from workspace_app.api.events import MessageDelta
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

AGENT = "/agents/sales/agent.ai.yaml"
SALES = b"""\
view: agent
title: Sales helper
description: Order questions.
instructions: Always cite the order id.
tools: [read_file, list_files, kb_search]
suggestions: [Late orders]
"""


class _Runner:
    """Records each turn's prompt and context, and what `read_skill` loads."""

    def __init__(self) -> None:
        self.turns: list[tuple[str, object, str]] = []

    async def run(self, prompt, ctx):  # noqa: ANN001 — test double
        loaded = await read_skill_impl(RunContextWrapper(ctx), "grill")
        self.turns.append((prompt, ctx, loaded))
        yield MessageDelta(text="ok")


def _app(holder: dict[str, str]):
    spec = make_spec(default_user=lambda: holder["id"])
    runner = _Runner()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec, runner


def _source(spec, by: str = "bob") -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(RcaInvestigation(title="Line 3", owner=by)).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


def _put(client, iid: str, path: str, body: bytes) -> None:
    assert client.put(_wp(iid, f"/files{path}"), content=body).status_code == 204, path


def _skill(name: str, text: str) -> bytes:
    return f"---\nname: {name}\ndescription: {text}\n---\n{text} body".encode()


def _workspace(client, src: str) -> None:
    _put(client, src, AGENT, SALES)
    _put(client, src, "/.skill/grill/SKILL.md", _skill("grill", "root grill"))
    _put(client, src, "/.skill/tidy/SKILL.md", _skill("tidy", "root tidy"))
    _put(client, src, "/agents/sales/.skill/grill/SKILL.md", _skill("grill", "own grill"))
    _put(client, src, "/agents/sales/.skill/quote/SKILL.md", _skill("quote", "own quote"))


def _test_chat(client, src: str) -> str:
    r = client.post(_wp(src, "/chats"), json={"title": "Test", "agent_path": AGENT})
    assert r.status_code == 201, r.text
    return r.json()["chat_id"]


def _send(client, runner, src: str, chat: str | None = None):
    url = _wp(src, f"/chats/{chat}/messages") if chat else _wp(src, "/messages")
    r = client.post(url, json={"content": "hello"})
    assert r.status_code in (200, 202), r.text
    return runner.turns[-1]


def _advertised(prompt: str) -> list[str]:
    block = prompt.split("## Skills in this workspace", 1)[1].split("\n\n## ", 1)[0]
    return sorted(re.findall(r"^- `([^`]+)`: (.*)$", block, re.M))


def test_a_test_chat_runs_as_the_agent_in_the_authors_own_workspace():
    holder = {"id": "bob"}
    client, spec, runner = _app(holder)
    src = _source(spec)
    _workspace(client, src)

    _prompt, ctx, loaded = _send(client, runner, src, _test_chat(client, src))

    assert "Always cite the order id." in ctx.agent_config.system_prompt
    assert sorted(ctx.agent_config.allowed_tools) == ["list_files", "read_file"]
    # Its own skill wins over the root's of the same name.
    assert "own grill body" in loaded


def test_the_authors_other_chats_are_not_the_agent():
    holder = {"id": "bob"}
    client, spec, runner = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _test_chat(client, src)

    prompt, ctx, loaded = _send(client, runner, src)

    assert "Always cite the order id." not in ctx.agent_config.system_prompt
    assert "root grill body" in loaded
    assert ("quote", "own quote") not in _advertised(prompt)


def test_a_test_chat_advertises_exactly_the_skills_a_conversation_copy_does():
    """Parity with the copy as the oracle: the test chat merges the folder's
    skills at the turn, the copy has them at its root (`copy_plan`)."""
    holder = {"id": "bob"}
    client, spec, runner = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    tested, *_ = _send(client, runner, src, _test_chat(client, src))
    r = client.post(
        _wp(src, "/publish"),
        json={"path": AGENT, "sharing": "per_conversation", "reach": "everyone"},
    )
    assert r.status_code == 200, r.text
    holder["id"] = "amy"
    copy = client.post("/published/open", json={"item_id": src, "path": AGENT}).json()["item_id"]

    copied, _ctx, loaded = _send(client, runner, copy)

    assert _advertised(tested) == _advertised(copied)
    assert ("grill", "own grill") in _advertised(copied)
    assert "own grill body" in loaded


def test_a_test_chat_must_name_an_agent_this_item_holds():
    holder = {"id": "bob"}
    client, spec, _runner = _app(holder)
    src = _source(spec)
    _put(client, src, "/agents/broken/agent.ai.yaml", b"view: agent\ntitle: A\n")

    missing = client.post(_wp(src, "/chats"), json={"agent_path": "/nowhere/agent.ai.yaml"})
    broken = client.post(_wp(src, "/chats"), json={"agent_path": "/agents/broken/agent.ai.yaml"})

    assert missing.status_code == 404, missing.text
    assert broken.status_code == 422, broken.text
    assert "instructions" in broken.text


def _summary(client, src: str, path: str = AGENT) -> dict:
    r = client.get(_wp(src, "/agent"), params={"path": path})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_summary_says_what_a_conversation_will_hold():
    holder = {"id": "bob"}
    client, spec, runner = _app(holder)
    src = _source(spec)
    _workspace(client, src)

    got = _summary(client, src)
    _p, ctx, _l = _send(client, runner, src, _test_chat(client, src))

    assert got["valid"] is True
    assert (got["title"], got["description"], got["narrows"]) == (
        "Sales helper",
        "Order questions.",
        True,
    )
    # The tools are the test chat's own, and what it named but cannot have
    # is said.
    assert sorted(got["tools"]) == sorted(ctx.agent_config.allowed_tools)
    assert got["not_available"] == ["kb_search"]
    assert {"grill", "quote", "tidy"} <= set(got["skills"])
    assert got["suggestions"] == [{"label": "Late orders", "prompt": "Late orders"}]
    assert got["published"] is None


def test_the_summary_of_a_file_that_is_not_an_agent_yet_says_why():
    holder = {"id": "bob"}
    client, spec, _runner = _app(holder)
    src = _source(spec)
    _put(client, src, AGENT, b"view: agent\ntitle: A\ninstructions: x\nmodel: gpt\n")
    _put(client, src, "/b/agent.ai.yaml", b"view: agent\n")

    typo = _summary(client, src)
    broken = _summary(client, src, "/b/agent.ai.yaml")

    assert typo["valid"] is True and "model" in typo["problems"][0]
    assert broken["valid"] is False
    assert any("title" in p for p in broken["problems"])
    assert client.get(_wp(src, "/agent"), params={"path": "/none.yaml"}).status_code == 404


def test_the_summary_says_how_it_is_published_with_its_latest_notes_and_usage():
    holder = {"id": "bob"}
    client, spec, _runner = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    body = {"path": AGENT, "sharing": "per_conversation", "reach": "everyone"}
    for note in ("first", "", "third"):
        assert client.post(_wp(src, "/publish"), json={**body, "note": note}).status_code == 200
    for who in ("amy", "amy", "carl"):
        holder["id"] = who
        assert (
            client.post("/published/open", json={"item_id": src, "path": AGENT}).status_code == 200
        )
    holder["id"] = "bob"

    got = _summary(client, src)

    assert got["published"]["reach"] == "everyone"
    assert (got["opens"], got["people"]) == (3, 2)
    assert [n["text"] for n in got["notes"]] == ["third", "first"]


def test_a_wui_published_at_the_same_path_is_not_the_agents_state():
    """A row of another kind at this path is not this agent's publication."""
    holder = {"id": "bob"}
    client, spec, _runner = _app(holder)
    src = _source(spec)
    _put(client, src, AGENT, b"view: wui\ntitle: Board\n")
    r = client.post(_wp(src, "/publish"), json={"path": AGENT, "sharing": "fresh", "reach": "me"})
    assert r.status_code == 200, r.text
    _put(client, src, AGENT, SALES)

    assert _summary(client, src)["published"] is None


def test_a_test_chat_whose_agent_file_broke_runs_as_the_app_without_its_folders_skills():
    holder = {"id": "bob"}
    client, spec, runner = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    chat = _test_chat(client, src)
    _put(client, src, AGENT, b"view: [")

    prompt, ctx, loaded = _send(client, runner, src, chat)

    assert "Always cite the order id." not in ctx.agent_config.system_prompt
    assert ("quote", "own quote") not in _advertised(prompt)
    assert "root grill body" in loaded


def test_in_a_copy_the_copys_own_agent_wins_over_a_test_chat_naming_another():
    holder = {"id": "bob"}
    client, spec, runner = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    other = "/agents/other/agent.ai.yaml"
    _put(client, src, other, b"view: agent\ntitle: Other\ninstructions: Be someone else.\n")
    r = client.post(
        _wp(src, "/publish"),
        json={"path": AGENT, "sharing": "per_conversation", "reach": "everyone"},
    )
    assert r.status_code == 200, r.text
    holder["id"] = "amy"
    copy = client.post("/published/open", json={"item_id": src, "path": AGENT}).json()["item_id"]
    chat = client.post(_wp(copy, "/chats"), json={"agent_path": other})
    assert chat.status_code == 201, chat.text

    _p, ctx, _l = _send(client, runner, copy, chat.json()["chat_id"])

    assert "Always cite the order id." in ctx.agent_config.system_prompt
    assert "Be someone else." not in ctx.agent_config.system_prompt


def test_read_skill_is_granted_when_only_the_agents_folder_holds_a_skill():
    """The grant follows what the turn advertises: with every App skill off
    and none at the root, the folder's skill is the only one — and the test
    chat must still hold the tool that loads it."""
    from workspace_app.apps.skills import effective_item_skills

    holder = {"id": "bob"}
    client, spec, runner = _app(holder)
    app_skills = [s.name for s in effective_item_skills("rca", "default", {}, [], tools=None)]
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        src = rm.create(
            RcaInvestigation(
                title="Line 3", owner="bob", attached_skill_prefs={n: False for n in app_skills}
            )
        ).resource_id
    _put(client, src, AGENT, SALES)
    _put(client, src, "/agents/sales/.skill/quote/SKILL.md", _skill("quote", "own quote"))
    _p, plain, _l = _send(client, runner, src)
    assert plain.skills_reachable is False

    _p, ctx, _l = _send(client, runner, src, _test_chat(client, src))

    assert ctx.skills_reachable is True
