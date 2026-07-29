"""The item's environment variables reach the tools, and nothing else.

They are named per dispatch rather than left somewhere in the sandbox: a file
would be readable by the item uid, which is the same uid the agent's own `exec`
runs as, so it could never separate the two — and where the hand-over did not
happen it separated nothing, it just failed for everybody.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents.tool_context import ToolContext
from agents.usage import Usage

from workspace_app.agent import AgentToolContext
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec
from workspace_app.tooling.registry import CommandInfo, PackageInfo, _to_function_tool

_PKG = PackageInfo(
    name="pkg",
    install_dir="/.tools/pkg",
    commands=(),
)
_CMD = CommandInfo(name="do", description="d", params_json_schema={})


async def _dispatch(env_vars: dict[str, str]) -> MockSandbox:
    sandbox = MockSandbox()
    handle = await sandbox.create(SandboxSpec())
    actx = AgentToolContext(
        investigation_id="inv",
        sandbox=sandbox,
        handle=handle,
        user_env=env_vars,
    )
    tool = _to_function_tool(_PKG, _CMD)
    tctx: ToolContext[Any] = ToolContext(
        context=actx, tool_name="do", tool_call_id="id", tool_arguments="{}", usage=Usage()
    )
    await tool.on_invoke_tool(tctx, "{}")
    return sandbox


@pytest.mark.asyncio
async def test_a_dispatched_tool_receives_the_items_variables():
    sandbox = await _dispatch({"API_KEY": "sk-1"})
    assert sandbox.exec_envs[-1]["API_KEY"] == "sk-1"


@pytest.mark.asyncio
async def test_an_item_with_none_set_adds_nothing():
    """Absent and empty must look the same to the backend — an empty mapping is
    not a request to clear the command's environment."""
    sandbox = await _dispatch({})
    assert sandbox.exec_envs[-1] == {}


@pytest.mark.asyncio
async def test_the_chart_re_render_gets_them_too():
    """#285 re-renders a chart by running the SAME command again. It is the
    second dispatch site, and the one a call-site change forgets — a key that
    worked on the first render and vanished on the second would look like a
    flaky tool, not a missing variable."""
    from workspace_app.tooling import registry

    sandbox = MockSandbox()
    handle = await sandbox.create(SandboxSpec())
    actx = AgentToolContext(
        investigation_id="inv", sandbox=sandbox, handle=handle, user_env={"API_KEY": "sk-1"}
    )
    await registry._exec_tool(actx, handle, _PKG, "chart", "{}")
    assert sandbox.exec_envs[-1]["API_KEY"] == "sk-1"


@pytest.mark.asyncio
async def test_the_dispatch_names_which_keys_the_user_set():
    """The launcher restores exactly these names last, so a user still wins on
    `PYTHONPATH` / `HOME` / `PIP_USER`. Without the list the launcher has
    nothing to put back and the carrier silently wins instead — "stored,
    listed, no effect"."""
    sandbox = await _dispatch({"API_KEY": "sk-1", "REGION": "tw"})
    sent = sandbox.exec_envs[-1]
    assert set(sent["SANDBOX_USER_ENV_KEYS"].split()) == {"API_KEY", "REGION"}


@pytest.mark.asyncio
async def test_nothing_set_names_nothing():
    sandbox = await _dispatch({})
    assert "SANDBOX_USER_ENV_KEYS" not in sandbox.exec_envs[-1]
