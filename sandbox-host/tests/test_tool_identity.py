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
    await sb.exec(
        h,
        ["../.tools/erp/launch"],
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
