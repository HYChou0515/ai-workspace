"""A third-party tool is dispatched AS ITSELF (docs/plan-tool-identity-secrets.md
P1): the platform names the tool on the exec, so a backend with a second identity
runs it as that one. What that exec may contain — its argv, which of the user's
names reach it — is the sandbox host's to enforce
(`sandbox-host/tests/test_as_tool_is_enforced_here.py`), because its API can be
called without this app.

First-party tools, `python` and view plugins are out of scope and must behave
exactly as before — so each assertion here has its first-party twin.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents.tool_context import ToolContext
from agents.usage import Usage

from workspace_app.agent import AgentToolContext
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec
from workspace_app.tooling.registry import (
    CommandInfo,
    PackageInfo,
    _to_function_tool,
    _tool_env,
    exec_package_command,
)

_CMD = CommandInfo(name="q", description="d", params_json_schema={})
_THIRD = PackageInfo(name="erp", install_dir="../.tools/erp", commands=(), third_party=True)
_FIRST = PackageInfo(name="sci-plot", install_dir="../.tools/sci-plot", commands=())


async def _dispatch(pkg: PackageInfo, env_vars: dict[str, str] | None = None) -> MockSandbox:
    sandbox = MockSandbox()
    handle = await sandbox.create(SandboxSpec())
    actx = AgentToolContext(
        investigation_id="inv", sandbox=sandbox, handle=handle, user_env=env_vars or {}
    )
    tool = _to_function_tool(pkg, _CMD)
    tctx: ToolContext[Any] = ToolContext(
        context=actx, tool_name="q", tool_call_id="id", tool_arguments="{}", usage=Usage()
    )
    await tool.on_invoke_tool(tctx, "{}")
    return sandbox


@pytest.mark.asyncio
async def test_a_third_party_tool_is_dispatched_as_itself():
    sandbox = await _dispatch(_THIRD)
    assert sandbox.exec_tools[-1] == "erp"


@pytest.mark.asyncio
async def test_a_first_party_tool_is_dispatched_as_the_sandbox():
    sandbox = await _dispatch(_FIRST)
    assert sandbox.exec_tools[-1] is False


_LOADERS = {
    "PYTHONPATH": "/root/evil",
    "PYTHONSTARTUP": "/root/evil.py",
    "LD_PRELOAD": "/root/evil.so",
    "LD_LIBRARY_PATH": "/root",
    "HOME": "/root",
    "PATH": "/root/bin",
    "SANDBOX_HOME": "/root",
    "BASH_ENV": "/root/evil.sh",
    "NODE_OPTIONS": "--require /root/evil.js",
}


def test_the_app_hands_every_name_the_user_set_to_the_host():
    """#581 / the reserved-names decision: the user wins for the platform's own
    tools, PYTHONPATH and HOME included — and the app no longer filters for a
    third-party one either; the host does (one rule, one place)."""
    env = _tool_env({**_LOADERS, "ERP_TOKEN": "t"}, item_id="inv")
    for name, value in _LOADERS.items():
        assert env[name] == value
    assert set(env["SANDBOX_USER_ENV_KEYS"].split()) == {*_LOADERS, "ERP_TOKEN"}


@pytest.mark.asyncio
async def test_the_dispatch_leaves_a_first_party_tools_env_alone():
    sandbox = await _dispatch(_FIRST, {"PYTHONPATH": "/mine"})
    assert sandbox.exec_envs[-1]["PYTHONPATH"] == "/mine"


# ── view plugins (#847/#848): a plain PackageInfo, so the MOUNT decides ───────


async def _plugin_dispatch(mounted: dict[str, str]) -> MockSandbox:
    """A view plugin's command, dispatched the way both plugin call sites do:
    a bare `PackageInfo` (never `third_party`), in a context whose sandbox spec
    names what is mounted from the third-party store."""
    sandbox = MockSandbox()
    handle = await sandbox.create(SandboxSpec(tools=mounted))
    actx = AgentToolContext(
        investigation_id="inv",
        sandbox=sandbox,
        handle=handle,
        sandbox_spec=SandboxSpec(tools=mounted),
        user_env={"PYTHONPATH": "/root/evil"},
    )
    pkg = PackageInfo(name="wafermap", install_dir="../.tools/wafermap", commands=())
    await exec_package_command(actx, handle, pkg, "validate", "{}")
    return sandbox


@pytest.mark.asyncio
async def test_an_artifact_view_plugin_runs_as_the_tool_identity():
    """An `{artifact: url}` plugin is resolved like an external tool, so its
    bundle sits in the third-party store — which only the tool group may read. Run
    as the sandbox uid it could no longer read its own files."""
    sandbox = await _plugin_dispatch({"wafermap": "a" * 64})
    assert sandbox.exec_tools[-1] == "wafermap"


@pytest.mark.asyncio
async def test_a_bundled_view_plugin_runs_as_the_sandbox():
    """A `{bundle: ...}` plugin ships in `builtin/`: first-party, untouched."""
    sandbox = await _plugin_dispatch({})
    assert sandbox.exec_tools[-1] is False
    assert sandbox.exec_envs[-1]["PYTHONPATH"] == "/root/evil"
