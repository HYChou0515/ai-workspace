"""A third-party tool runs as the item's TOOL identity, not the sandbox's
(docs/plan-tool-identity-secrets.md P1).

The agent's own commands run as the sandbox uid; a third-party tool's command
runs as a second pooled uid that shares the sandbox's cgroup and workspace (by
ACL) but nothing else — its own HOME, and a group the sandbox uid never holds.
Being a different uid is what keeps the agent from reading the running tool's
environment (`/proc/<pid>/environ` is readable by the same uid only).

L2 unit tests: the uid pool is pure and the privileged steps are seamed, so
nothing here needs root. Real enforcement is in the root-gated integration file.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pytest

from sandbox_host.isolated_process import IsolatedProcessSandbox, _acl_argv
from sandbox_host.protocol import SandboxSpec

TOOL_GID = 4242


@pytest.fixture
def tooled(tmp_path):
    """Room in the pool for exactly one sandbox's TWO uids: the sandbox takes the
    caller's own uid (the pool hands out from the top) so `_provision`'s raw
    `chown` of the workspace works non-root; the tool takes the one below, whose
    ownership handovers are recorded through `_own_privately` instead."""
    acl: list[list[str]] = []
    owned: list[tuple[Path, int]] = []
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        uid_min=os.getuid() - 1,
        uid_max=os.getuid(),
        tool_gid=TOOL_GID,
        acl_runner=acl.append,
        chown_runner=lambda p, u: None,
    )
    sb._own_privately = lambda path, uid: owned.append((path, uid))  # ty: ignore[invalid-assignment]
    return sb, acl, owned


async def test_a_sandbox_gets_a_second_pooled_uid_for_its_tools(tooled):
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    ident = sb._identities[h.id]
    assert ident.uid == os.getuid()
    assert ident.tool_uid == os.getuid() - 1


async def test_the_workspace_acl_grants_both_identities(tooled):
    sb, acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    ident = sb._identities[h.id]
    assert ident.tool_uid is not None
    ws = sb._workspace(h)
    assert acl == [_acl_argv(ws, ident.uid, ident.tool_uid)]


def test_one_acl_call_names_every_identity_and_its_default():
    spec = "u:7:rwx,u:8:rwx"
    assert _acl_argv(Path("/w"), 7, 8) == ["setfacl", "-R", "-m", spec, "-d", "-m", spec, "/w"]


async def test_the_tool_identity_has_its_own_home(tooled):
    sb, _acl, owned = tooled
    h = await sb.create(SandboxSpec())
    ident = sb._identities[h.id]
    tool_home = sb._require(h) / ".tool-home"
    assert tool_home.is_dir()
    assert (tool_home, ident.tool_uid) in owned
    # Never the sandbox's own HOME: that one the agent writes.
    assert (sb._require(h) / ".home", ident.tool_uid) not in owned


async def test_a_tool_command_runs_as_the_tool_identity_in_the_same_cgroup(tooled):
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    ident = sb._identities[h.id]
    plain, _, _ = sb._exec_argv(h, ["true"])
    argv, cwd, env = sb._exec_argv(h, ["../.tools/erp/launch", "q", "{}"], as_tool="erp")

    assert argv[:3] == plain[:3], "joins the SAME cgroup — limits and billing unchanged"
    assert f"--reuid={ident.tool_uid}" in argv
    assert f"--regid={ident.tool_uid}" in argv
    assert f"--groups={TOOL_GID}" in argv
    assert "--clear-groups" not in argv
    assert argv[-3:] == ["../.tools/erp/launch", "q", "{}"]
    assert cwd == sb._workspace(h)
    tool_home = str(sb._require(h) / ".tool-home")
    assert env["HOME"] == tool_home
    assert env["SANDBOX_HOME"] == tool_home
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONSAFEPATH"] == "1"


async def test_an_ordinary_command_keeps_the_sandbox_identity(tooled):
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    ident = sb._identities[h.id]
    argv, _cwd, env = sb._exec_argv(h, ["true"])
    assert f"--reuid={ident.uid}" in argv
    assert "--clear-groups" in argv
    assert not any(a.startswith("--groups") for a in argv)
    assert env["HOME"] == str(sb._require(h) / ".home")
    assert "PYTHONNOUSERSITE" not in env


async def test_the_tool_identity_owns_its_home_on_every_tool_exec(tooled):
    """Same shape as `.home`: re-established per exec, so a home the exec path
    had to recreate is never handed to the tool owned by this service."""
    sb, _acl, owned = tooled
    h = await sb.create(SandboxSpec())
    ident = sb._identities[h.id]
    owned.clear()
    sb._exec_argv(h, ["true"], as_tool="erp")
    assert (sb._require(h) / ".tool-home", ident.tool_uid) in owned


async def test_kill_returns_both_uids_to_the_pool(tooled, monkeypatch):
    sb, _acl, _owned = tooled
    # Freed uids come back in a different order, so the second sandbox may get
    # the uid that is not the caller's — `_provision`'s raw chown would then need
    # root. Ownership is not what this test is about.
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    h = await sb.create(SandboxSpec())
    await sb.kill(h)
    # A pool of exactly two: a leaked tool uid makes this create raise.
    h2 = await sb.create(SandboxSpec())
    ident = sb._identities[h2.id]
    assert {ident.uid, ident.tool_uid} == {os.getuid(), os.getuid() - 1}


async def test_the_callers_env_cannot_move_a_tools_home(tooled, monkeypatch):
    """The caller's variables win over the exec path's own — except the names
    that define the tool identity's environment. A `HOME` the agent can write is
    a user site the tool would import from."""
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    seen: dict[str, str] = {}

    async def fake_spawn(*_argv, env, **_kw):
        seen.update(env)
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    # No tool is mounted in this fixture; what an as_tool exec may be is pinned
    # in test_as_tool_is_enforced_here.py. This test is about HOME only.
    monkeypatch.setattr(sb, "_refuse_tool_exec", lambda *_a: None)
    await sb.exec(
        h,
        ["../.tools/erp/launch", "q", "{}"],
        env={"HOME": "/root/evil", "SANDBOX_HOME": "/root/evil", "PYTHONNOUSERSITE": "0"},
        as_tool="erp",
    )
    tool_home = str(sb._require(h) / ".tool-home")
    assert seen["HOME"] == tool_home
    assert seen["SANDBOX_HOME"] == tool_home
    assert seen["PYTHONNOUSERSITE"] == "1"


async def test_an_ordinary_commands_env_still_wins(tooled, monkeypatch):
    """The pin is for tool commands only: the agent's own `exec` keeps the
    precedence it always had."""
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    seen: dict[str, str] = {}

    async def fake_spawn(*_argv, env, **_kw):
        seen.update(env)
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    await sb.exec(h, ["true"], env={"HOME": "/elsewhere"})
    assert seen["HOME"] == "/elsewhere"


async def test_without_a_tool_group_a_tool_command_runs_as_the_sandbox(tmp_path, caplog):
    """A host built without a tool group cannot separate the two; it says so once,
    at construction, rather than pretending."""
    with caplog.at_level(logging.WARNING):
        sb = IsolatedProcessSandbox(
            root_dir=tmp_path / "sb",
            cgroup_root=tmp_path / "cg",
            uid_min=os.getuid(),
            uid_max=os.getuid(),
            acl_runner=lambda argv: None,
            chown_runner=lambda p, u: None,
        )
    assert "third-party tools run as the sandbox's own uid" in caplog.text
    h = await sb.create(SandboxSpec())
    assert sb._identities[h.id].tool_uid is None
    argv, _cwd, _env = sb._exec_argv(h, ["true"], as_tool="erp")
    assert f"--reuid={os.getuid()}" in argv
    assert "--clear-groups" in argv


async def test_a_failed_create_gives_back_every_uid_and_leaves_no_dir(tmp_path, monkeypatch):
    """Review round 1: a pool with room for the sandbox uid but not the tool's
    raised after taking the first one, which was then never freed."""
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        uid_min=os.getuid(),
        uid_max=os.getuid(),
        tool_gid=TOOL_GID,
        acl_runner=lambda argv: None,
        chown_runner=lambda p, u: None,
    )
    with pytest.raises(RuntimeError, match="exhausted"):
        await sb.create(SandboxSpec())
    assert sb._pool._used == set()
    assert list((tmp_path / "sb").iterdir()) == []


async def test_a_create_that_fails_later_gives_its_uids_back_too(tooled, monkeypatch):
    sb, _acl, _owned = tooled

    def boom(*_a, **_k):
        raise OSError("setfacl failed")

    monkeypatch.setattr(sb, "_provision", boom)
    with pytest.raises(OSError):
        await sb.create(SandboxSpec())
    assert sb._pool._used == set()


def _record_around(sb, monkeypatch):
    """Record, in order, the per-call widenings, any root walk, and the spawn."""
    order: list[str] = []
    monkeypatch.setattr(
        sb, "_run_as", lambda uid, argv: order.append(f"as {uid}: {' '.join(argv[:3])}")
    )
    monkeypatch.setattr(sb, "_acl_runner", lambda argv: order.append("ROOT setfacl"))
    monkeypatch.setattr(sb, "_chown_runner", lambda path, uid: order.append("ROOT chown"))

    async def fake_spawn(*_argv, **_kw):
        order.append("run")
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    return order


async def test_around_a_tool_call_each_identity_opens_its_own_files_to_the_other(
    tooled, monkeypatch
):
    """Review round 1 (regression, reproduced as root): a mode set after creation
    shrinks a file's ACL mask, so the tool could not write where the agent had
    unpacked an archive, and the agent could not read the tool's 0600 file.

    Review round 2 (reproduced as root): fixing that with a ROOT `setfacl -R` or
    `chown` walk over the workspace lets a process the agent left running swap a
    file for a symlink mid-walk and have root grant it any file on the host. So
    each identity widens ITS OWN files' group class (`chmod g+rwX` sets the mask
    on a file that has an ACL), as itself: the sandbox uid before the call, the
    tool uid after. A race can then only make a uid change what it already owns."""
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    ident = sb._identities[h.id]
    order = _record_around(sb, monkeypatch)
    monkeypatch.setattr(sb, "_refuse_tool_exec", lambda *_a: None)

    await sb.exec(h, ["../.tools/erp/launch", "q", "{}"], as_tool="erp")

    assert order == [
        f"as {ident.uid}: find {sb._workspace(h)} (",
        "run",
        f"as {ident.tool_uid}: find {sb._workspace(h)} -user",
    ]


async def test_no_root_walk_happens_around_a_tool_call(tooled, monkeypatch):
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    order = _record_around(sb, monkeypatch)
    monkeypatch.setattr(sb, "_refuse_tool_exec", lambda *_a: None)
    await sb.exec(h, ["../.tools/erp/launch", "q", "{}"], as_tool="erp")
    assert not [o for o in order if o.startswith("ROOT")]


async def test_the_widening_runs_on_the_workspace_and_nowhere_else(tooled, monkeypatch):
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    seen: list[list[str]] = []
    monkeypatch.setattr(sb, "_run_as", lambda uid, argv: seen.append(argv))
    monkeypatch.setattr(sb, "_refuse_tool_exec", lambda *_a: None)

    async def fake_spawn(*_argv, **_kw):
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    await sb.exec(h, ["../.tools/erp/launch", "q", "{}"], as_tool="erp")
    ws = str(sb._workspace(h))
    ident = sb._identities[h.id]
    # Before: the agent's directories, and the files its group can already read
    # (644 -> 664) — so the tool can create where the agent unpacked and edit
    # what it could edit before; a file the agent made 600 (an SSH key) stays
    # 600, as it chose (review round 2, M1). After: the tool's OWN files only.
    assert seen == [
        [
            "find",
            ws,
            "(",
            "-type",
            "d",
            "-o",
            "-perm",
            "-g+r",
            ")",
            "-exec",
            "chmod",
            "g+rwX",
            "{}",
            "+",
        ],
        ["find", ws, "-user", str(ident.tool_uid), "-exec", "chmod", "g+rwX", "{}", "+"],
    ]


async def test_a_refused_tool_call_touches_nothing(tooled, monkeypatch):
    """Not a walk of the workspace for every request the agent sends itself."""
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    order = _record_around(sb, monkeypatch)
    result = await sb.exec(h, ["sh", "-c", "id"], as_tool="erp")
    assert result.exit_code == 126
    assert order == []


async def test_the_agents_own_command_walks_nothing(tooled, monkeypatch):
    sb, _acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    order = _record_around(sb, monkeypatch)
    await sb.exec(h, ["true"])
    assert order == ["run"]


async def test_reown_after_a_restore_is_chown_only_as_it_was(tooled, monkeypatch):
    """Round 2 removed the ACL re-apply round 1 added to `reown`: a restore keeps
    the archived 770/660 modes (verified as root in round 1), so it bought
    nothing — and it was one more root `setfacl -R` over an agent's tree."""
    sb, acl, _owned = tooled
    h = await sb.create(SandboxSpec())
    acl.clear()
    await sb.reown(h)
    assert acl == []
