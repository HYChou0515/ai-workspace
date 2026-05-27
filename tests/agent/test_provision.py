import json
import shutil
from pathlib import Path

import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.provision import (
    ProvisionError,
    ToolDef,
    build_argv,
    build_provisioned_tools,
    provision_tools,
)
from workspace_app.sandbox.protocol import ExecResult, SandboxHandle, SandboxSpec

_REPO = Path(__file__).resolve().parents[2]
_TOOLS = _REPO / "sample-tools"

_FETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "enum": ["alloy-batches", "sensor-telemetry"]},
        "rows": {"type": "integer"},
        "out": {"type": "string"},
        "json": {"type": "boolean"},
    },
}
_DEF = ToolDef(
    name="data-fetch",
    description="materialise a named dataset",
    invoke=["uv", "run", "data-fetch"],
    setup=[["uv", "sync"]],
    positional=["name"],
    params_json_schema=_FETCH_SCHEMA,
)


# ---------------------------- a tiny recording sandbox ----------------------------
class _Recording:
    """Minimal Sandbox stand-in: records exec argv, returns canned results."""

    def __init__(self, results: dict[tuple[str, ...], ExecResult] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._results = results or {}

    async def create(self, spec: SandboxSpec) -> SandboxHandle:  # pragma: no cover
        return SandboxHandle(id="s1")

    async def exec(self, handle, cmd, on_output=None) -> ExecResult:
        self.calls.append(cmd)
        return self._results.get(tuple(cmd), ExecResult(exit_code=0, stdout=b"ok"))

    async def kill(self, handle) -> None:  # pragma: no cover
        return None


async def test_ensure_sandbox_provisions_only_allowed_tools():
    from workspace_app.resources.agent_config import AgentConfig

    sb = _Recording()
    ctx = AgentToolContext(
        sandbox=sb,  # ty: ignore[invalid-argument-type]
        agent_config=AgentConfig(name="a", allowed_tools=["t1"]),
        tool_defs=[
            ToolDef(name="t1", description="", invoke=["run-t1"], setup=[["echo", "install-t1"]]),
            ToolDef(name="t2", description="", invoke=["run-t2"], setup=[["echo", "install-t2"]]),
        ],
    )
    await ctx.ensure_sandbox()
    assert ["echo", "install-t1"] in sb.calls  # allowed → installed
    assert ["echo", "install-t2"] not in sb.calls  # not in allowed_tools → skipped


def test_agent_for_adds_allowed_provisioned_tools():
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    agent = _agent_for(
        AgentConfig(name="a", allowed_tools=["exec", "t1"]),
        tool_defs=[ToolDef(name="t1", description="d", invoke=["run-t1"])],
    )
    names = {t.name for t in agent.tools}
    assert "t1" in names  # provisioned tool the agent can call
    assert "exec" in names  # built-in still there
    assert "data-fetch" not in names  # an un-allowed def is absent


def test_build_argv_positional_then_flags_then_bools():
    assert build_argv(_DEF, {"name": "alloy-batches", "rows": 60, "json": True}) == [
        "uv",
        "run",
        "data-fetch",
        "alloy-batches",
        "--rows",
        "60",
        "--json",
    ]
    # a false bool + an absent optional are omitted
    assert build_argv(_DEF, {"name": "sensor-telemetry", "json": False}) == [
        "uv",
        "run",
        "data-fetch",
        "sensor-telemetry",
    ]


async def test_provision_runs_each_setup_step():
    sb = _Recording()
    await provision_tools(sb, SandboxHandle(id="s1"), [_DEF])
    assert sb.calls == [["uv", "sync"]]


async def test_provision_raises_on_nonzero_exit():
    sb = _Recording({("uv", "sync"): ExecResult(exit_code=1, stdout=b"boom")})
    with pytest.raises(ProvisionError) as exc:
        await provision_tools(sb, SandboxHandle(id="s1"), [_DEF])
    assert exc.value.tool == "data-fetch"


async def test_provisioned_tool_execs_invoke_argv_in_sandbox():
    sb = _Recording()
    actx = AgentToolContext(sandbox=sb, handle=SandboxHandle(id="s1"))  # ty: ignore[invalid-argument-type]
    [tool] = build_provisioned_tools([_DEF])
    assert tool.name == "data-fetch"
    assert tool.params_json_schema["properties"]["name"]["enum"]  # enum reaches the model

    args = json.dumps({"name": "alloy-batches", "rows": 5})
    out = await tool.on_invoke_tool(RunContextWrapper(actx), args)
    assert sb.calls[-1] == ["uv", "run", "data-fetch", "alloy-batches", "--rows", "5"]
    assert "ok" in out


# --------- integration: provision + chain the two real example tools ---------
def _tooldef(dir_name: str, tool: str, positional: list[str], schema: dict) -> ToolDef:
    proj = str(_TOOLS / dir_name)
    return ToolDef(
        name=tool,
        description=f"example provisioned tool: {tool}",
        setup=[["uv", "sync", "--project", proj]],
        invoke=["uv", "run", "--project", proj, tool],
        positional=positional,
        params_json_schema=schema,
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
async def test_two_tools_provision_and_chain_in_a_real_sandbox(tmp_path: Path):
    """End-to-end: install both example tools INTO a real sandbox, then fetch a
    dataset with one and summarise it with the other — files flow through the
    sandbox workspace, deps stay in the tools' own venvs."""
    from workspace_app.sandbox.local_process import LocalProcessSandbox

    fetch = _tooldef("data-fetch", "data-fetch", ["name"], _FETCH_SCHEMA)
    summarise = _tooldef(
        "csv-column-summary",
        "csv-column-summary",
        ["csv"],
        {"type": "object", "properties": {"csv": {"type": "string"}, "json": {"type": "boolean"}}},
    )

    sandbox = LocalProcessSandbox(root_dir=tmp_path / "sbx", isolate=False)
    handle = await sandbox.create(SandboxSpec())
    try:
        await provision_tools(sandbox, handle, [fetch, summarise])
        actx = AgentToolContext(sandbox=sandbox, handle=handle)  # ty: ignore[invalid-argument-type]
        tools = {t.name: t for t in build_provisioned_tools([fetch, summarise])}
        ctx = RunContextWrapper(actx)

        fetched = await tools["data-fetch"].on_invoke_tool(
            ctx, json.dumps({"name": "alloy-batches", "rows": 60})
        )
        assert "60 rows" in fetched

        summarised = await tools["csv-column-summary"].on_invoke_tool(
            ctx, json.dumps({"csv": "alloy-batches.csv"})
        )
        assert "60 rows" in summarised and "columns" in summarised
    finally:
        await sandbox.kill(handle)
