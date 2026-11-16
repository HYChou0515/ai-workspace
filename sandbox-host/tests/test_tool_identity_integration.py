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


@pytest.fixture(autouse=True)
def _traversable(tmp_path):
    """pytest makes its temp dirs 0700 root, so no dropped uid could follow the
    `/.tools` symlink chain (it resolves to an absolute path under here). In
    production that chain is traversable — every sandbox runs
    `../.tools/python-stack/launch` through it — so open the ancestors the same
    way. Root-only tests, run in a throwaway environment."""
    for node in [tmp_path, *tmp_path.parents]:
        if node == node.parent:
            break
        node.chmod(node.stat().st_mode | 0o755)


#: A test bundle whose launcher runs its arguments as a shell script — the
#: author's own code, standing in for a real tool. Round 1 made the host accept
#: an `as_tool` exec only in a tool call's shape, so every probe here goes
#: through `launch <command> <args>`, as a real call does.
_SCRIPTED = b'#!/bin/sh\nexec sh -c "$2"\n'


@pytest.fixture
def isolated(tmp_path):
    tools = tmp_path / "scripted-tools"
    sha = "b" * 64
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("launch")
        info.size = len(_SCRIPTED)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(_SCRIPTED))
    ToolCache(tools / EXT_DIR, harden=_GroupHardener(TOOL_GID)).ensure(sha, buf.getvalue())
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        tool_gid=TOOL_GID,
        uid_min=100001,
        uid_max=100010,
        memory_max="128M",
        cpu_cores=1.0,
        pids_max=128,
        tools_dir=tools,
    )
    sb.mounts = {"erp": sha}  # ty: ignore[unresolved-attribute]
    return sb


async def _tool(sb, h, script: str, **kw):
    return await sb.exec(h, ["../.tools/erp/launch", "run", script], as_tool="erp", **kw)


@requires_isolation
async def test_a_tool_command_runs_as_the_tool_uid_holding_the_tool_group(isolated):
    h = await isolated.create(SandboxSpec(tools=isolated.mounts))
    ident = isolated._identities[h.id]
    result = await _tool(isolated, h, "id -u; id -G")
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
    h = await isolated.create(SandboxSpec(tools=isolated.mounts))
    tool = await _tool(isolated, h, "mkdir -p out && echo t > out/report.txt")
    assert tool.exit_code == 0, tool.stderr
    agent = await isolated.exec(
        h, ["sh", "-c", "echo a >> out/report.txt && echo a > mine.txt && rm out/report.txt"]
    )
    assert agent.exit_code == 0, agent.stderr
    back = await _tool(isolated, h, "echo t >> mine.txt && cat mine.txt")
    assert back.exit_code == 0, back.stderr
    assert back.stdout == b"a\nt\n"


@requires_isolation
async def test_the_agent_cannot_read_a_running_tools_environment(isolated):
    """The reason for a second uid at all: `/proc/<pid>/environ` answers only to
    the same uid (or root), and a tool's credentials live there while it runs."""
    h = await isolated.create(SandboxSpec(tools=isolated.mounts))
    started = await _tool(
        isolated,
        h,
        "sleep 20 >/dev/null 2>&1 & echo $!",
        env={"MACHINE_PASSWORD": "hunter2"},
    )
    assert started.exit_code == 0, started.stderr
    pid = started.stdout.strip().decode()
    assert pid.isdigit(), started.stdout
    try:
        peek = await isolated.exec(h, ["cat", f"/proc/{pid}/environ"])
        assert peek.exit_code != 0
        assert b"hunter2" not in peek.stdout
    finally:
        await isolated.kill(h)


@requires_isolation
async def test_a_tools_temp_file_never_reaches_the_agent(isolated):
    """Review round 3 (reproduced as root): a tool's temp file used to sit in the
    workspace. It is under the tool's own HOME now, which the agent cannot enter
    — during the call or after it."""
    h = await isolated.create(SandboxSpec(tools=isolated.mounts))
    tool = await _tool(isolated, h, 't=$(mktemp); echo s3cret > "$t"; echo "$t"')
    assert tool.exit_code == 0, tool.stderr
    path = tool.stdout.strip().decode()
    assert "/.tool-home/tmp/" in path, path
    peek = await isolated.exec(h, ["sh", "-c", f"cat {path}; grep -r s3cret . 2>/dev/null"])
    assert b"s3cret" not in peek.stdout


@requires_isolation
async def test_the_agent_cannot_write_the_tools_home(isolated):
    h = await isolated.create(SandboxSpec(tools=isolated.mounts))
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
    result = await sb.exec(h, ["../.tools/erp/launch", "q", "{}"], as_tool="erp")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == f"ran-as-{ident.tool_uid}".encode()


@requires_isolation
async def test_a_first_party_tool_stays_readable_by_the_agent(with_tools):
    sb, mounts = with_tools
    h = await sb.create(SandboxSpec(tools=mounts))
    result = await sb.exec(h, ["cat", "../.tools/sci-plot/README"])
    assert result.exit_code == 0, result.stderr


_WRITER = (
    b"#!/bin/sh\nset -e\n"
    b"echo out > data/out.csv\n"  # create in a 755 dir the agent made
    b"echo x >> data/in.csv\n"  # write a 644 file the agent made
    b"if cat data/secret 2>/dev/null; then exit 9; fi\n"  # a 600 file stays private
    b"umask 077; echo result > atomic.tmp; mv atomic.tmp atomic.csv\n"  # 0600 atomic write
)


@requires_isolation
async def test_modes_set_after_creation_do_not_lock_either_identity_out(tmp_path):
    """Review rounds 1-2 (reproduced as root): modes set after creation shrink the
    ACL mask. Around each tool call each identity widens its own files as itself
    — so the tool works in what the agent unpacked, and the agent can read, edit
    and delete what the tool wrote."""
    tools = tmp_path / "tools"
    sha = "c" * 64
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("launch")
        info.size = len(_WRITER)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(_WRITER))
    ToolCache(tools / EXT_DIR, harden=_GroupHardener(TOOL_GID)).ensure(sha, buf.getvalue())
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        tool_gid=TOOL_GID,
        uid_min=100001,
        uid_max=100010,
        tools_dir=tools,
    )
    h = await sb.create(SandboxSpec(tools={"erp": sha}))
    setup = await sb.exec(
        h,
        [
            "sh",
            "-c",
            "mkdir data && echo in > data/in.csv && echo s > data/secret "
            "&& chmod 755 data && chmod 644 data/in.csv && chmod 600 data/secret",
        ],
    )
    assert setup.exit_code == 0, setup.stderr
    tool = await sb.exec(h, ["../.tools/erp/launch", "q", "{}"], as_tool="erp")
    assert tool.exit_code == 0, tool.stderr
    agent = await sb.exec(
        h, ["sh", "-c", "cat atomic.csv && echo more >> atomic.csv && rm atomic.csv"]
    )
    assert agent.exit_code == 0, agent.stderr
    assert agent.stdout == b"result\n"
