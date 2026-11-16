"""The tool identity, L3: REAL privilege separation (docs/plan-tool-identity-
secrets.md P1). The unit tests assert the argv; these prove the kernel agrees.

Needs root + `setpriv` + `setfacl`, so it is `integration`-tagged and skipped
everywhere else (same arrangement as `test_isolated_process_integration.py`).
"""

from __future__ import annotations

import os
import shutil

import pytest

from sandbox_host.isolated_process import IsolatedProcessSandbox
from sandbox_host.protocol import SandboxSpec

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
