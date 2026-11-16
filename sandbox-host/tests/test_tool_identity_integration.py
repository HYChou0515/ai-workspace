"""The tool identity, L3: REAL privilege separation (docs/plan-tool-identity-
secrets.md P1). The unit tests assert the argv; these prove the kernel agrees.

Needs root + `setpriv` + `setfacl`, so it is `integration`-tagged and skipped
everywhere else (same arrangement as `test_isolated_process_integration.py`).
"""

from __future__ import annotations

import io
import os
import shutil
import tarfile

import pytest

from sandbox_host.isolated_process import IsolatedProcessSandbox
from sandbox_host.protocol import SandboxSpec
from sandbox_host.tool_cache import BUILTIN_DIR, EXT_DIR, ToolCache, _GroupHardener

pytestmark = pytest.mark.integration

_CAN_ISOLATE = os.getuid() == 0 and bool(shutil.which("setpriv")) and bool(shutil.which("setfacl"))
requires_isolation = pytest.mark.skipif(not _CAN_ISOLATE, reason="needs root + setpriv + setfacl")

TOOL_GID = 100000


@pytest.fixture
def isolated(tmp_path):
    return IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        tool_gid=TOOL_GID,
        uid_min=100001,
        uid_max=100010,
        memory_max="128M",
        cpu_cores=1.0,
        pids_max=128,
    )


@requires_isolation
async def test_a_tool_command_runs_as_the_tool_uid_holding_the_tool_group(isolated):
    h = await isolated.create(SandboxSpec())
    ident = isolated._identities[h.id]
    result = await isolated.exec(h, ["sh", "-c", "id -u; id -G"], as_tool="erp")
    assert result.exit_code == 0, result.stderr
    uid, groups = result.stdout.decode().split("\n")[:2]
    assert uid == str(ident.tool_uid)
    assert str(TOOL_GID) in groups.split()
    # …and the agent's own command holds neither.
    mine = await isolated.exec(h, ["sh", "-c", "id -u; id -G"])
    uid, groups = mine.stdout.decode().split("\n")[:2]
    assert uid == str(ident.uid)
    assert str(TOOL_GID) not in groups.split()


@requires_isolation
async def test_each_identity_can_edit_and_delete_what_the_other_wrote(isolated):
    h = await isolated.create(SandboxSpec())
    tool = await isolated.exec(
        h, ["sh", "-c", "mkdir -p out && echo t > out/report.txt"], as_tool="erp"
    )
    assert tool.exit_code == 0, tool.stderr
    agent = await isolated.exec(
        h, ["sh", "-c", "echo a >> out/report.txt && echo a > mine.txt && rm out/report.txt"]
    )
    assert agent.exit_code == 0, agent.stderr
    back = await isolated.exec(h, ["sh", "-c", "echo t >> mine.txt && cat mine.txt"], as_tool="erp")
    assert back.exit_code == 0, back.stderr
    assert back.stdout == b"a\nt\n"


@requires_isolation
async def test_the_agent_cannot_read_a_running_tools_environment(isolated):
    """The reason for a second uid at all: `/proc/<pid>/environ` answers only to
    the same uid (or root), and a tool's credentials live there while it runs."""
    h = await isolated.create(SandboxSpec())
    started = await isolated.exec(
        h,
        ["sh", "-c", "sleep 20 >/dev/null 2>&1 & echo $!"],
        env={"MACHINE_PASSWORD": "hunter2"},
        as_tool="erp",
    )
    pid = started.stdout.strip().decode()
    try:
        peek = await isolated.exec(h, ["cat", f"/proc/{pid}/environ"])
        assert peek.exit_code != 0
        assert b"hunter2" not in peek.stdout
    finally:
        await isolated.kill(h)


@requires_isolation
async def test_the_agent_cannot_write_the_tools_home(isolated):
    h = await isolated.create(SandboxSpec())
    tool_home = isolated._require(h) / ".tool-home"
    result = await isolated.exec(h, ["sh", "-c", f"mkdir -p {tool_home}/.local/x"])
    assert result.exit_code != 0


def _bundle() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body, mode in (
            ("launch", b"#!/bin/sh\necho ran-as-$(id -u)\n", 0o755),
            ("creds.py", b"PASSWORD = 'hunter2'\n", 0o644),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = mode
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


@pytest.fixture
def with_tools(tmp_path):
    """P2: a host whose third-party store is hardened for the tool group, plus
    one first-party tool baked in the way the image ships them."""
    tools = tmp_path / "tools"
    sha = "e" * 64
    ToolCache(tools / EXT_DIR, harden=_GroupHardener(TOOL_GID)).ensure(sha, _bundle())
    builtin = tools / BUILTIN_DIR / "sci-plot"
    builtin.mkdir(parents=True)
    (builtin / "README").write_text("first-party\n")
    tools.chmod(0o755)
    (tools / BUILTIN_DIR).chmod(0o755)
    builtin.chmod(0o755)
    (builtin / "README").chmod(0o644)
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        tool_gid=TOOL_GID,
        uid_min=100001,
        uid_max=100010,
        tools_dir=tools,
    )
    return sb, {"erp": sha}


@requires_isolation
async def test_the_agent_can_neither_read_nor_run_a_third_party_tool(with_tools):
    sb, mounts = with_tools
    h = await sb.create(SandboxSpec(tools=mounts))
    read = await sb.exec(h, ["cat", "../.tools/erp/creds.py"])
    assert read.exit_code != 0
    assert b"hunter2" not in read.stdout
    run = await sb.exec(h, ["../.tools/erp/launch"])
    assert run.exit_code != 0


@requires_isolation
async def test_the_tool_identity_runs_it(with_tools):
    sb, mounts = with_tools
    h = await sb.create(SandboxSpec(tools=mounts))
    ident = sb._identities[h.id]
    result = await sb.exec(h, ["../.tools/erp/launch"], as_tool="erp")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == f"ran-as-{ident.tool_uid}".encode()


@requires_isolation
async def test_a_first_party_tool_stays_readable_by_the_agent(with_tools):
    sb, mounts = with_tools
    h = await sb.create(SandboxSpec(tools=mounts))
    result = await sb.exec(h, ["cat", "../.tools/sci-plot/README"])
    assert result.exit_code == 0, result.stderr
