"""P5 of `docs/plan-wui-publish-and-agent-hub.md`: running as an agent.

A conversation copy of a published agent runs as THAT agent: its
`instructions:` join the App's system prompt, its `tools:` narrow the turn's
tools within what the item holds — never past it (A4) — and its folder's
skills and workflows are the copy's, its own winning on a clash (A5). A copy
is one conversation (A2): a second one is a new copy, not a new chat.
"""

from __future__ import annotations

import asyncio

from workspace_app.api import create_app
from workspace_app.api.events import MessageDelta
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.resolve import resolve_item_agent_config
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

AGENT = "/agents/sales/agent.ai.yaml"
SALES = b"""\
view: agent
title: Sales helper
instructions: Always answer in Chinese and cite the order id.
tools: [read_file, list_files, kb_search]
"""


class _Runner:
    """Records the context every turn ran with."""

    def __init__(self) -> None:
        self.seen: list = []

    async def run(self, prompt, ctx):  # noqa: ANN001 — test double
        self.seen.append(ctx)
        yield MessageDelta(text="ok")


def _app(holder: dict[str, str]):
    spec = make_spec(default_user=lambda: holder["id"])
    store = MemoryFileStore()
    runner = _Runner()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=store,
        runner=runner,
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec, store, runner, app


def _source(spec, by: str = "bob") -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(RcaInvestigation(title="Line 3", owner=by)).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


def _put(client, iid: str, path: str, body: bytes) -> None:
    assert client.put(_wp(iid, f"/files{path}"), content=body).status_code == 204, path


def _published_copy(client, holder, src: str, agent: bytes = SALES) -> str:
    _put(client, src, AGENT, agent)
    r = client.post(
        _wp(src, "/publish"),
        json={"path": AGENT, "sharing": "per_conversation", "reach": "everyone"},
    )
    assert r.status_code == 200, r.text
    holder["id"] = "amy"
    opened = client.post("/published/open", json={"item_id": src, "path": AGENT})
    assert opened.status_code == 200, opened.text
    return opened.json()["item_id"]


def _turn(client, runner, iid: str):
    before = len(runner.seen)
    r = client.post(_wp(iid, "/messages"), json={"content": "hello"})
    assert r.status_code in (200, 202), r.text
    assert len(runner.seen) == before + 1
    return runner.seen[-1]


def test_a_conversation_copy_runs_with_the_agents_instructions():
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    src = _source(spec)
    copy = _published_copy(client, holder, src)

    ctx = _turn(client, runner, copy)

    assert "Always answer in Chinese and cite the order id." in ctx.agent_config.system_prompt
    assert "Sales helper" in ctx.agent_config.system_prompt


def test_its_tools_narrow_within_what_the_item_holds_and_never_widen():
    """`kb_search` is not an RCA item's tool: naming it adds nothing (A4)."""
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    src = _source(spec)
    app_tools = set(_turn(client, runner, src).agent_config.allowed_tools)
    assert {"read_file", "list_files"} <= app_tools
    assert "kb_search" not in app_tools
    copy = _published_copy(client, holder, src)

    tools = _turn(client, runner, copy).agent_config.allowed_tools

    assert sorted(tools) == ["list_files", "read_file"]


def test_without_tools_it_holds_what_the_app_gives_the_item():
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    src = _source(spec)
    app_tools = _turn(client, runner, src).agent_config.allowed_tools
    copy = _published_copy(
        client, holder, src, b"view: agent\ntitle: Plain\ninstructions: Be brief.\n"
    )

    assert _turn(client, runner, copy).agent_config.allowed_tools == app_tools


def test_the_source_workspace_itself_is_not_the_agent():
    """Publishing does not change the author's own chat: an agent.ai.yaml in
    a folder is a file there, not what the workspace runs as."""
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    src = _source(spec)
    _put(client, src, AGENT, SALES)

    ctx = _turn(client, runner, src)

    assert "Always answer in Chinese" not in ctx.agent_config.system_prompt


def test_a_copy_whose_agent_file_was_broken_runs_as_the_app_and_does_not_fail():
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    src = _source(spec)
    copy = _published_copy(client, holder, src)
    _put(client, copy, AGENT, b"view: [")

    ctx = _turn(client, runner, copy)

    assert "Always answer in Chinese" not in ctx.agent_config.system_prompt


def test_its_folders_skills_and_workflows_are_the_copys_its_own_winning():
    holder = {"id": "bob"}
    client, spec, store, _runner, _ = _app(holder)
    src = _source(spec)
    _put(client, src, "/.skill/grill/SKILL.md", b"---\nname: grill\ndescription: root\n---\nroot")
    _put(client, src, "/.skill/grill/references/old.md", b"root only")
    _put(client, src, "/.skill/tidy/SKILL.md", b"---\nname: tidy\ndescription: t\n---\nt")
    _put(
        client,
        src,
        "/agents/sales/.skill/grill/SKILL.md",
        b"---\nname: grill\ndescription: own\n---\nown",
    )
    _put(client, src, "/.workflows/weekly.json", b'{"root": true}')
    _put(client, src, "/agents/sales/.workflows/weekly.json", b'{"own": true}')
    _put(client, src, "/agents/sales/.workflows/daily.json", b'{"own": true}')

    copy = _published_copy(client, holder, src)
    files = store._files[copy]

    assert files["/.skill/grill/SKILL.md"].endswith(b"own")
    # The agent's skill replaces the root's WHOLE — a root file it does not
    # have is not left inside it.
    assert "/.skill/grill/references/old.md" not in files
    assert "/.skill/tidy/SKILL.md" in files
    assert files["/.workflows/weekly.json"] == b'{"own": true}'
    assert files["/.workflows/daily.json"] == b'{"own": true}'
    # The folder itself is copied as it is.
    assert "/agents/sales/.skill/grill/SKILL.md" in files


def test_a_wui_copy_is_not_overlaid():
    holder = {"id": "bob"}
    client, spec, store, _runner, _ = _app(holder)
    src = _source(spec)
    page = "/pages/report/page.ai.yaml"
    _put(client, src, page, b"view: wui\ntitle: Board\n")
    _put(client, src, "/pages/report/.skill/grill/SKILL.md", b"page's")
    r = client.post(
        _wp(src, "/publish"), json={"path": page, "sharing": "fresh", "reach": "everyone"}
    )
    assert r.status_code == 200, r.text
    holder["id"] = "amy"
    copy = client.post("/published/open", json={"item_id": src, "path": page}).json()["item_id"]

    assert "/.skill/grill/SKILL.md" not in store._files[copy]


def test_a_copy_is_one_conversation():
    """A2/A11: a new conversation with the agent is a new copy, opened from
    Agent Hub — the copy refuses a second chat."""
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    src = _source(spec)
    copy = _published_copy(client, holder, src)
    _turn(client, runner, copy)

    r = client.post(_wp(copy, "/chats"), json={"title": "another"})

    assert r.status_code == 409, r.text
    # The source workspace is an ordinary one.
    holder["id"] = "bob"
    assert client.post(_wp(src, "/chats"), json={"title": "another"}).status_code == 201


def test_publishing_an_agent_file_with_a_problem_is_refused_and_says_what():
    holder = {"id": "bob"}
    client, spec, _store, _runner, _ = _app(holder)
    src = _source(spec)
    for body, said in (
        (b"view: agent\ntitle: A\n", "instructions"),
        (b"view: agent\ntitle: A\ninstructions: x\ninstruction: typo\n", "instruction"),
    ):
        _put(client, src, AGENT, body)
        r = client.post(
            _wp(src, "/publish"),
            json={"path": AGENT, "sharing": "per_conversation", "reach": "everyone"},
        )
        assert r.status_code == 422, r.text
        assert said in r.text


def test_a_workflow_turn_in_a_copy_is_narrowed_too():
    """The agent bounds what its copy can do, not only its chat: a workflow
    step there holds no tool the agent left out."""
    holder = {"id": "bob"}
    client, spec, _store, _runner, app = _app(holder)
    src = _source(spec)
    copy = _published_copy(client, holder, src)
    turn_ctx = app.state.turn_ctx
    cfg = resolve_item_agent_config(spec, app.state.app_catalog, copy)

    async def build():
        return await turn_ctx.build_workflow_turn(
            copy, agent_config=cfg, run_subagent=None, history_messages=[]
        )

    ctx = asyncio.run(build())

    assert sorted(ctx.agent_config.allowed_tools) == ["list_files", "read_file"]


def test_a_copy_whose_agent_file_was_deleted_runs_as_the_app():
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    src = _source(spec)
    copy = _published_copy(client, holder, src)
    assert client.delete(_wp(copy, f"/files{AGENT}")).status_code == 204

    ctx = _turn(client, runner, copy)

    assert "Always answer in Chinese" not in ctx.agent_config.system_prompt


def test_its_description_tells_the_model_what_it_is_for():
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    src = _source(spec)
    copy = _published_copy(
        client,
        holder,
        src,
        b"view: agent\ntitle: Sales helper\ndescription: Order questions.\ninstructions: Help.\n",
    )

    prompt = _turn(client, runner, copy).agent_config.system_prompt

    assert "Order questions." in prompt
    assert prompt.index("Order questions.") < prompt.index("Help.")


def test_a_tool_the_agent_left_out_is_not_offered_on_request():
    """#480 tells the model which tools the item turned off, so it can ask the
    person to switch one on. In an agent's copy that only holds for a tool
    the agent HAS: switching on one it left out would not reach the turn."""
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        src = rm.create(
            RcaInvestigation(
                title="Line 3",
                owner="bob",
                attached_tool_prefs={"exec": False, "write_file": False},
            )
        ).resource_id
    assert {"exec", "write_file"} <= set(_turn(client, runner, src).agent_config.disabled_tools)
    copy = _published_copy(
        client,
        holder,
        src,
        b"view: agent\ntitle: A\ninstructions: Help.\ntools: [read_file, exec]\n",
    )

    disabled = _turn(client, runner, copy).agent_config.disabled_tools

    assert "exec" in disabled
    assert "write_file" not in disabled


def test_a_wui_copy_is_an_ordinary_workspace_with_as_many_chats_as_wanted():
    holder = {"id": "bob"}
    client, spec, _store, runner, _ = _app(holder)
    src = _source(spec)
    page = "/pages/report/page.ai.yaml"
    _put(client, src, page, b"view: wui\ntitle: Board\n")
    r = client.post(
        _wp(src, "/publish"), json={"path": page, "sharing": "fresh", "reach": "everyone"}
    )
    assert r.status_code == 200, r.text
    holder["id"] = "amy"
    copy = client.post("/published/open", json={"item_id": src, "path": page}).json()["item_id"]
    _turn(client, runner, copy)

    assert client.post(_wp(copy, "/chats"), json={"title": "another"}).status_code == 201


def test_copy_plan_writes_each_destination_once():
    """A root workflow the agent also has is not copied and then overwritten:
    each destination is written once, so the copy's bytes are counted once."""
    from workspace_app.api.publish_copy import copy_plan

    plan = copy_plan(["/.workflows/w.json", "/a/.workflows/w.json", "/x.txt"], "/a/agent.ai.yaml")

    assert plan == [
        ("/a/.workflows/w.json", ["/a/.workflows/w.json", "/.workflows/w.json"]),
        ("/x.txt", ["/x.txt"]),
    ]
    assert copy_plan(["/x.txt"], None) == [("/x.txt", ["/x.txt"])]
