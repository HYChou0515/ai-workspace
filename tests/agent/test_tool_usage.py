"""Counting every AI tool call (docs/plan-tool-usage-stats.md P2).

One wrap around every tool `_agent_for` hands the model — the place every
source is still known — counts each real call once: which tool, from which
source, whether it failed, how long it took, and for whom / where.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agents.tool_context import ToolContext
from agents.usage import Usage

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tool_usage import SEP, ToolUsageRecorder, is_failure
from workspace_app.resources.agent_config import AgentConfig
from workspace_app.sandbox.protocol import ExecResult, SandboxHandle
from workspace_app.tooling.registry import CommandInfo, PackageInfo

# ── what counts as a failure (plan §3.3) ────────────────────────────────────


@pytest.mark.parametrize(
    ("result", "failed"),
    [
        ("Asked the user: which one?", False),
        ("Tool `spc` returned (exit_code=0):\nok", False),
        ("Tool `spc` returned (exit_code=2) — bad args:\n...", True),
        ("Tool `spc` returned (exit_code=-9):\nkilled", True),
        ("An error occurred while running the tool. Please try again. Error: boom", True),
        ("error: pass exactly one of `query` or `url`", True),
        ("Error: the arguments you sent for `x` were not valid JSON", True),
        ("  error: leading space still an error", True),
        ("The file says: error: something", False),
        (None, False),
        (["structured", "output"], False),
    ],
)
def test_what_counts_as_a_failure(result: Any, failed: bool) -> None:
    assert is_failure(result) is failed


# ── the recorder ────────────────────────────────────────────────────────────


def test_the_recorder_sums_by_day_tool_and_source_with_breakdowns() -> None:
    rec = ToolUsageRecorder(day=lambda: "2026-10-14")
    rec.record(
        "spc",
        "first_party",
        failed=False,
        ms=100,
        user="alice",
        surface="chat",
        app="rca",
        item="i1",
    )
    rec.record(
        "spc",
        "first_party",
        failed=True,
        ms=300,
        user="bob",
        surface="schedule",
        app="rca",
        item="i2",
    )
    rec.record(
        "exec", "builtin", failed=False, ms=5, user="alice", surface="chat", app="pm", item="i3"
    )

    rows = {(r.tool, r.source): r for r in rec.drain()}

    spc = rows[("spc", "first_party")]
    assert (spc.day, spc.calls, spc.fails, spc.total_ms) == ("2026-10-14", 2, 1, 400)
    assert spc.by_user == {"alice": [1, 0, 100], "bob": [1, 1, 300]}
    assert spc.by_surface_app == {"chat∕rca": [1, 0, 100], "schedule∕rca": [1, 1, 300]}
    assert spc.by_item == {"i1": [1, 0, 100], "i2": [1, 1, 300]}
    assert rows[("exec", "builtin")].calls == 1


def test_draining_hands_the_counts_over_once() -> None:
    rec = ToolUsageRecorder(day=lambda: "2026-10-14")
    rec.record("exec", "builtin", failed=False, ms=1, user="u", surface="chat", app="a", item="i")

    assert len(rec.drain()) == 1
    assert rec.drain() == []


# ── the wrap, through the runner's own agent assembly ───────────────────────


def _pkg(name: str, cmd: str, *, third_party: bool = False) -> PackageInfo:
    return PackageInfo(
        name=name,
        commands=(CommandInfo(name=cmd, description="d", params_json_schema={"type": "object"}),),
        install_dir=f"../.tools/{name}",
        third_party=third_party,
    )


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        self.t += 0.25  # every reading a quarter second later
        return self.t


def _agent(*packages: PackageInfo):  # noqa: ANN202
    from workspace_app.api.litellm_runner import _agent_for

    names = ["ask_user", *(p.name for p in packages)]
    return _agent_for(
        AgentConfig(name="a", model="ollama_chat/x", allowed_tools=names),
        packages=list(packages),
        in_chat=True,
    )


def _ctx(rec: ToolUsageRecorder | None, **kw: Any) -> AgentToolContext:
    return AgentToolContext(
        sandbox=object(),  # ty: ignore[invalid-argument-type]
        handle=SandboxHandle(id="s1"),
        tool_usage=rec,
        investigation_id="item-1",
        app_slug="rca",
        acting_user="alice",
        surface="chat",
        **kw,
    )


async def _call(agent, actx: AgentToolContext, name: str, args: str) -> Any:  # noqa: ANN001
    tool = next(t for t in agent.tools if t.name == name)
    ctx = ToolContext(
        context=actx, tool_name=name, tool_call_id="c1", tool_arguments=args, usage=Usage()
    )
    return await tool.on_invoke_tool(ctx, args)


@pytest.fixture
def exec_exits(monkeypatch):  # noqa: ANN001, ANN201
    """Package commands run with a chosen exit code, no sandbox."""
    from workspace_app.tooling import registry

    codes: list[int] = []

    async def fake_exec(_actx, _handle, _pkg, _cmd, _args):  # noqa: ANN001, ANN202
        return ExecResult(exit_code=codes.pop(0), stdout=b"out")

    async def handle(self, **_kw):  # noqa: ANN001, ANN202, ARG001
        return SandboxHandle(id="s1")

    monkeypatch.setattr(registry, "_exec_tool", fake_exec)
    monkeypatch.setattr(AgentToolContext, "ensure_sandbox", handle)
    return codes


async def test_every_source_is_counted_under_its_own_name(exec_exits) -> None:  # noqa: ANN001
    rec = ToolUsageRecorder(day=lambda: "d", clock=_Clock())
    agent = _agent(_pkg("rca-tools", "spc"), _pkg("acme", "lookup", third_party=True))
    exec_exits.extend([0, 3])

    await _call(
        agent,
        _ctx(rec),
        "ask_user",
        json.dumps(
            {
                "questions": [
                    {
                        "question": "q?",
                        "header": "h",
                        "options": [
                            {"label": "a", "description": ""},
                            {"label": "b", "description": ""},
                        ],
                    }
                ]
            }
        ),
    )
    await _call(agent, _ctx(rec), "spc", "{}")
    await _call(agent, _ctx(rec), "acme__lookup", "{}")

    got = {(r.tool, r.source): (r.calls, r.fails) for r in rec.drain()}
    assert got == {
        ("ask_user", "builtin"): (1, 0),
        ("spc", "first_party"): (1, 0),
        ("acme__lookup", "third_party"): (1, 1),  # exit 3
    }


async def test_a_rule_granted_tool_is_a_built_in() -> None:
    rec = ToolUsageRecorder(day=lambda: "d", clock=_Clock())
    agent = _agent()

    await _call(agent, _ctx(rec), "ask_outside", json.dumps({"why": "w"}))  # refused: no query/url

    [row] = rec.drain()
    assert (row.tool, row.source, row.fails) == ("ask_outside", "builtin", 1)


async def test_the_time_is_what_the_call_took(exec_exits) -> None:  # noqa: ANN001
    rec = ToolUsageRecorder(day=lambda: "d", clock=_Clock())
    agent = _agent(_pkg("rca-tools", "spc"))
    exec_exits.append(0)

    await _call(agent, _ctx(rec), "spc", "{}")

    [row] = rec.drain()
    assert row.total_ms == 250  # two readings, a quarter second apart


async def test_arguments_the_tool_never_saw_are_a_failed_call() -> None:
    from workspace_app.agent.args_recovery import MalformedToolArgsError

    rec = ToolUsageRecorder(day=lambda: "d", clock=_Clock())
    agent = _agent()

    with pytest.raises(MalformedToolArgsError):
        await _call(agent, _ctx(rec), "ask_user", "{not json")

    [row] = rec.drain()
    assert (row.tool, row.calls, row.fails) == ("ask_user", 1, 1)


async def test_an_ambiguous_old_name_the_tool_never_ran_is_a_failed_call(exec_exits) -> None:  # noqa: ANN001
    """An old bare name two packages answer to: `ToolAliasModel` points the call
    at the first candidate with the collision as its arguments, and `safer`
    answers with the collision sentence without running anything. A call the
    tool never ran is a failure (§3.3) — this reply has no `error:` prefix
    (review r1)."""
    from workspace_app.agent.arg_repair import AMBIGUOUS_CALL_KEY

    rec = ToolUsageRecorder(day=lambda: "d", clock=_Clock())
    agent = _agent(_pkg("acme", "ls", third_party=True), _pkg("beta", "ls", third_party=True))
    args = {
        AMBIGUOUS_CALL_KEY: {
            "called": "ls",
            "candidates": [["acme", "acme__ls"], ["beta", "beta__ls"]],
        }
    }

    reply = await _call(agent, _ctx(rec), "acme__ls", json.dumps(args))

    assert "collision" in reply and exec_exits == []  # refused; nothing ran
    [row] = rec.drain()
    # Under the name the model called — not the first candidate it was routed
    # through, which never ran and would wear a failure it did not earn (r2).
    assert (row.tool, row.source, row.calls, row.fails) == ("ls", "third_party", 1, 1)
    assert row.package == ""  # two packages answer to "ls": it is neither's


async def test_who_where_and_which_item_come_from_the_turn(exec_exits) -> None:  # noqa: ANN001
    rec = ToolUsageRecorder(day=lambda: "d", clock=_Clock())
    agent = _agent(_pkg("rca-tools", "spc"))
    exec_exits.append(0)

    await _call(agent, _ctx(rec), "spc", "{}")

    [row] = rec.drain()
    assert set(row.by_user) == {"alice"}
    assert set(row.by_surface_app) == {"chat∕rca"}
    assert set(row.by_item) == {"item-1"}


async def test_a_turn_with_no_recorder_counts_nothing_and_still_works(exec_exits) -> None:  # noqa: ANN001
    agent = _agent(_pkg("rca-tools", "spc"))
    exec_exits.append(0)

    out = await _call(agent, _ctx(None), "spc", "{}")

    assert "exit_code=0" in out


async def test_the_runner_hands_every_turn_its_recorder() -> None:
    """The one place every surface passes through — app chat, KB chat,
    workflow steps, sub-agents and the KB sub-agent all run on the runner."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    rec = ToolUsageRecorder()
    actx = AgentToolContext()  # no config: the turn ends at once, after the hand-over

    [_ async for _ in LitellmAgentRunner(tool_usage=rec).run("hi", actx)]

    assert actx.tool_usage is rec


async def test_a_recorder_the_turn_already_has_is_kept() -> None:
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    mine = ToolUsageRecorder()
    actx = AgentToolContext(tool_usage=mine)

    [_ async for _ in LitellmAgentRunner(tool_usage=ToolUsageRecorder()).run("hi", actx)]

    assert actx.tool_usage is mine


# ── a wiki turn runs inside whoever asked it (review r1) ────────────────────


class _CaptureRunner:
    def __init__(self) -> None:
        self.ctxs: list[AgentToolContext] = []

    async def run(self, _q: str, ctx: AgentToolContext):  # noqa: ANN201
        self.ctxs.append(ctx)
        return
        yield  # an async generator that yields nothing


def _counted(name: str, body: Any) -> Any:  # noqa: ANN401
    from agents import FunctionTool

    from workspace_app.agent.tool_usage import wrap_with_usage

    async def invoke(ctx: Any, args: str) -> str:  # noqa: ANN401
        return await body(ctx, args)

    return wrap_with_usage(
        FunctionTool(
            name=name,
            description="",
            params_json_schema={"type": "object", "properties": {}},
            on_invoke_tool=invoke,
        ),
        "builtin",
    )


async def _invoke(tool: Any, actx: AgentToolContext) -> Any:  # noqa: ANN401
    ctx = ToolContext(
        context=actx, tool_name=tool.name, tool_call_id="c", tool_arguments="{}", usage=Usage()
    )
    return await tool.on_invoke_tool(ctx, "{}")


async def _ok(_ctx: Any, _args: str) -> str:  # noqa: ANN401
    return "ok"


async def test_the_wiki_reader_s_calls_count_where_the_question_was_asked() -> None:
    """`ask_wiki` → the reader, whose context carries the COLLECTION id as its
    `investigation_id` (the wiki store is keyed so) and names no surface. Its
    calls are the asking turn's: that item, that surface — not a collection id
    posing as an item, not background work nobody waits on."""
    from workspace_app.kb.wiki.reader import read_wiki

    rec = ToolUsageRecorder(day=lambda: "d", clock=_Clock())
    runner = _CaptureRunner()
    await read_wiki(
        runner,  # ty: ignore[invalid-argument-type]
        wiki_store=object(),  # ty: ignore[invalid-argument-type]
        wiki_sources=object(),  # ty: ignore[invalid-argument-type]
        collection_id="coll-9",
        question="q",
        agent_config=AgentConfig(name="r", model="m"),
    )
    [reader_ctx] = runner.ctxs
    reader_ctx.tool_usage = rec
    read_file = _counted("read_file", _ok)

    async def ask(_ctx: Any, _args: str) -> Any:  # noqa: ANN401
        return await _invoke(read_file, reader_ctx)

    await _invoke(_counted("ask_wiki", ask), _ctx(rec))

    rows = {r.tool: r for r in rec.drain()}
    inner = rows["read_file"]
    assert list(inner.by_item) == ["item-1"]
    assert list(inner.by_surface_app) == [f"chat{SEP}rca"]
    assert list(inner.by_user) == ["alice"]


async def test_a_wiki_pass_nobody_asked_for_is_background_with_no_item() -> None:
    from workspace_app.kb.wiki.maintainer import run_wiki_maintainer

    class _Store:
        _rm = None

        async def exists(self, *_a: Any) -> bool:  # noqa: ANN401
            return True

    rec = ToolUsageRecorder(day=lambda: "d", clock=_Clock())
    runner = _CaptureRunner()
    await run_wiki_maintainer(
        runner,  # ty: ignore[invalid-argument-type]
        wiki_store=_Store(),  # ty: ignore[invalid-argument-type]
        wiki_sources=object(),  # ty: ignore[invalid-argument-type]
        collection_id="coll-9",
        new_source="s",
        agent_config=AgentConfig(name="m", model="m"),
    )
    [ctx] = runner.ctxs
    ctx.tool_usage = rec

    await _invoke(_counted("write_file", _ok), ctx)

    [row] = rec.drain()
    assert list(row.by_item) == [""]
    assert list(row.by_surface_app) == [f"background{SEP}"]


async def test_each_command_is_counted_with_its_package(exec_exits) -> None:  # noqa: ANN001
    """§9.1: a package's commands carry the package's name, so a report can add
    them up per package; a built-in belongs to none."""
    rec = ToolUsageRecorder(day=lambda: "d", clock=_Clock())
    agent = _agent(_pkg("rca-tools", "spc"), _pkg("acme", "lookup", third_party=True))
    exec_exits.extend([0, 0])

    await _call(agent, _ctx(rec), "ask_outside", json.dumps({"why": "w"}))
    await _call(agent, _ctx(rec), "spc", "{}")
    await _call(agent, _ctx(rec), "acme__lookup", "{}")

    got = {r.tool: r.package for r in rec.drain()}
    assert got == {"ask_outside": "", "spc": "rca-tools", "acme__lookup": "acme"}
