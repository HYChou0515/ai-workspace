"""`as_tool` is enforced by the HOST, not trusted from the caller
(docs/plan-tool-identity-secrets.md, review round 1).

The host's API has no authentication and a sandbox shares the pod's network,
so the agent can POST to `/sandboxes/<its id>/exec` itself. If `as_tool` were
honoured as sent, that would be a shell as the tool identity: the tool's files,
and its credentials in the clear (reproduced in review). So an `as_tool` exec
may only be what a tool call is — the tool's own launcher, a command name and
its arguments — for a tool mounted from the third-party store, with an
environment the host itself scrubs. Called directly, the API then does nothing
the agent's own tool call could not.
"""

from __future__ import annotations

import io
import sys
import tarfile

import pytest

from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.protocol import SandboxSpec
from sandbox_host.tool_cache import BUILTIN_DIR, EXT_DIR, ToolCache
from sandbox_host.tool_env import scrub_tool_env

_LAUNCH = b"#!/bin/sh\nenv\n"


def _bundle() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("launch")
        info.size = len(_LAUNCH)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(_LAUNCH))
    return buf.getvalue()


@pytest.fixture
async def sandbox(tmp_path):
    tools = tmp_path / "tools"
    sha = "f" * 64
    ToolCache(tools / EXT_DIR, harden=lambda _p: None).ensure(sha, _bundle())
    builtin = tools / BUILTIN_DIR / "sci-plot"
    builtin.mkdir(parents=True)
    (builtin / "launch").write_bytes(_LAUNCH)
    (builtin / "launch").chmod(0o755)
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    handle = await sb.create(SandboxSpec(tools={"erp": sha}))
    return sb, handle


async def test_a_tool_call_shaped_exec_runs(sandbox):
    sb, h = sandbox
    result = await sb.exec(h, ["../.tools/erp/launch", "q", "{}"], as_tool="erp")
    assert result.exit_code == 0, result.stderr


@pytest.mark.parametrize(
    "cmd",
    [
        ["sh", "-c", "echo $MACHINE_PASSWORD"],
        [sys.executable, "-c", "print(1)"],
        ["../.tools/erp/launch"],
        ["../.tools/erp/launch", "q", "{}", "extra"],
        ["../.tools/erp/../erp/launch", "q", "{}"],
        ["../.tools/other/launch", "q", "{}"],
    ],
)
async def test_anything_else_as_the_tool_is_refused(sandbox, cmd):
    sb, h = sandbox
    result = await sb.exec(h, cmd, as_tool="erp")
    assert result.exit_code == 126
    assert b"only its own launcher" in result.stderr


async def test_a_first_party_tool_cannot_be_run_as_the_tool_identity(sandbox):
    """`builtin/` is readable by the sandbox uid anyway; naming one as `as_tool`
    can only be someone reaching for the identity, never a real dispatch."""
    sb, h = sandbox
    result = await sb.exec(h, ["../.tools/sci-plot/launch", "q", "{}"], as_tool="sci-plot")
    assert result.exit_code == 126


async def test_a_tool_not_mounted_in_this_sandbox_is_refused(sandbox):
    sb, h = sandbox
    result = await sb.exec(h, ["../.tools/ghost/launch", "q", "{}"], as_tool="ghost")
    assert result.exit_code == 126


async def test_the_callers_loader_names_never_reach_the_tool(sandbox):
    sb, h = sandbox
    result = await sb.exec(
        h,
        ["../.tools/erp/launch", "q", "{}"],
        env={
            "PYTHONPATH": "/root/evil",
            "OPENSSL_CONF": "/root/evil.cnf",
            "https_proxy": "http://evil:3128",
            "ERP_TOKEN": "t",
            "SANDBOX_USER_ENV_KEYS": "PYTHONPATH OPENSSL_CONF https_proxy ERP_TOKEN",
        },
        as_tool="erp",
    )
    out = result.stdout.decode()
    assert "PYTHONPATH=/root/evil" not in out
    assert "OPENSSL_CONF=" not in out
    assert "https_proxy=" not in out
    assert "ERP_TOKEN=t" in out
    assert "SANDBOX_USER_ENV_KEYS=ERP_TOKEN\n" in out


def test_the_scrub_drops_every_kind_of_loader_name_and_keeps_the_rest():
    kept = scrub_tool_env(
        {
            "LD_PRELOAD": "x",
            "PYTHONHOME": "x",
            "OPENSSL_MODULES": "x",
            "SSL_CERT_FILE": "x",
            "REQUESTS_CA_BUNDLE": "x",
            "HTTP_PROXY": "x",
            "all_proxy": "x",
            "GIT_SSH_COMMAND": "x",
            "PIP_INDEX_URL": "x",
            "UV_INDEX_URL": "x",
            "NODE_OPTIONS": "x",
            "JAVA_TOOL_OPTIONS": "x",
            "TMPDIR": "x",
            "BASH_FUNC_x%%": "x",
            "WAFER_API_TOKEN": "keep",
            "TOOL_DEBUG": "erp",
            "SANDBOX_USER_ENV_KEYS": "LD_PRELOAD WAFER_API_TOKEN TOOL_DEBUG",
        }
    )
    assert kept == {
        "WAFER_API_TOKEN": "keep",
        "TOOL_DEBUG": "erp",
        "SANDBOX_USER_ENV_KEYS": "WAFER_API_TOKEN TOOL_DEBUG",
    }


async def test_the_agents_own_commands_are_untouched(sandbox):
    """The rule is for `as_tool` only: the agent's own shell keeps every name."""
    sb, h = sandbox
    result = await sb.exec(h, ["sh", "-c", "echo $PYTHONPATH"], env={"PYTHONPATH": "/mine"})
    assert result.stdout == b"/mine\n"
