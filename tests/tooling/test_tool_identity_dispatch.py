"""A third-party tool is dispatched AS ITSELF (docs/plan-tool-identity-secrets.md
P1): the platform names the tool on the exec, so a backend with a second identity
runs it as that one, and its environment carries none of the user's names that
decide where code is loaded from.

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
    assert sandbox.exec_as_tools[-1] == "erp"


@pytest.mark.asyncio
async def test_a_first_party_tool_is_dispatched_as_the_sandbox():
    sandbox = await _dispatch(_FIRST)
    assert sandbox.exec_as_tools[-1] is None


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


def test_a_third_party_tool_gets_none_of_the_users_loader_names():
    """A name that decides where code is loaded from is a way to run the agent's
    code inside the tool, beside its credentials. Dropped from the values AND from
    the list the launcher restores from — or the launcher would restore it as
    empty over its own."""
    env = _tool_env({**_LOADERS, "ERP_TOKEN": "t"}, item_id="inv", third_party=True)
    for name in _LOADERS:
        assert name not in env, name
    assert env["ERP_TOKEN"] == "t"
    assert env["SANDBOX_USER_ENV_KEYS"].split() == ["ERP_TOKEN"]
    assert env["WORKSPACE_ITEM_ID"] == "inv"


def test_a_third_party_tool_with_only_loader_names_gets_no_key_list():
    env = _tool_env({"PYTHONPATH": "/root/evil"}, item_id="inv", third_party=True)
    assert env == {"WORKSPACE_ITEM_ID": "inv"}


def test_a_first_party_tool_still_gets_every_name_the_user_set():
    """#581 / the reserved-names decision: for the platform's own tools the user
    wins, PYTHONPATH and HOME included."""
    env = _tool_env({**_LOADERS, "ERP_TOKEN": "t"}, item_id="inv")
    for name, value in _LOADERS.items():
        assert env[name] == value
    assert set(env["SANDBOX_USER_ENV_KEYS"].split()) == {*_LOADERS, "ERP_TOKEN"}


@pytest.mark.asyncio
async def test_the_dispatch_applies_the_third_party_filter():
    sandbox = await _dispatch(_THIRD, {"PYTHONPATH": "/root/evil", "ERP_TOKEN": "t"})
    sent = sandbox.exec_envs[-1]
    assert "PYTHONPATH" not in sent
    assert sent["ERP_TOKEN"] == "t"


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
    assert sandbox.exec_as_tools[-1] == "wafermap"
    assert "PYTHONPATH" not in sandbox.exec_envs[-1]


@pytest.mark.asyncio
async def test_a_bundled_view_plugin_runs_as_the_sandbox():
    """A `{bundle: ...}` plugin ships in `builtin/`: first-party, untouched."""
    sandbox = await _plugin_dispatch({})
    assert sandbox.exec_as_tools[-1] is None
    assert sandbox.exec_envs[-1]["PYTHONPATH"] == "/root/evil"
