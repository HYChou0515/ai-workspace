"""IsolatedProcessSandbox — L2 unit tests (non-root, no real isolation).

Every privileged operation is seamed so it runs as the dev user: the uid pool
is pure; cgroup writes target an injected `cgroup_root` tmp dir; `chown` sets the
owner only (gid left as `-1`) with the sandbox uid = the test's own uid; and the
one true system-binary boundary (`setfacl`) is asserted as an argv and stubbed.
Real privilege-drop / cgroup enforcement lives in the root-gated L3 integration
test (`test_isolated_process_integration.py`).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sandbox_host.isolated_process import (
    IsolatedProcessSandbox,
    _acl_argv,
    _CgroupManager,
    _cpu_max,
    _parse_size,
    _run_chown,
    _run_setfacl,
    _setpriv_cgroup_argv,
    _UidPool,
)
from sandbox_host.protocol import SandboxSpec


@pytest.fixture
def isolated(tmp_path):
    """A sandbox whose uid pool is exactly the test's own uid (so `chown` is a
    no-op that works non-root), whose cgroup root is a tmp tree, and whose ACL
    application is captured instead of shelling out to `setfacl`."""
    calls: list[list[str]] = []
    chowns: list[tuple[Path, int]] = []
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        uid_min=os.getuid(),
        uid_max=os.getuid(),
        memory_max="64M",
        cpu_cores=0.5,
        pids_max=64,
        acl_runner=calls.append,
        chown_runner=lambda p, u: chowns.append((p, u)),
    )
    sb.acl_calls = calls  # ty: ignore[unresolved-attribute]
    sb.chown_calls = chowns  # ty: ignore[unresolved-attribute]
    return sb


def test_uid_pool_allocates_distinct_identities():
    pool = _UidPool(100, 102)
    a = pool.alloc()
    b = pool.alloc()
    assert a != b
    assert {a[0], b[0]} <= {100, 101, 102}


def test_uid_pool_reuses_after_free():
    pool = _UidPool(100, 100)  # a pool of exactly one
    uid, gid = pool.alloc()
    pool.free(uid, gid)
    again = pool.alloc()
    assert again == (uid, gid)


def test_uid_pool_exhaustion_raises():
    pool = _UidPool(100, 100)
    pool.alloc()
    with pytest.raises(RuntimeError, match="exhausted"):
        pool.alloc()


def test_uid_pool_free_unknown_is_noop():
    pool = _UidPool(100, 100)
    pool.free(999, 999)  # never allocated ⇒ ignored, not double-freed
    assert pool.alloc() == (100, 100)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1024", "1024"),
        ("512K", str(512 * 1024)),
        ("512M", str(512 * 1024 * 1024)),
        ("2G", str(2 * 1024**3)),
        ("max", "max"),
    ],
)
def test_parse_size(text: str, expected: str):
    assert _parse_size(text) == expected


@pytest.mark.parametrize(
    ("cores", "expected"),
    [(1.0, "100000 100000"), (0.5, "50000 100000"), (2.0, "200000 100000")],
)
def test_cpu_max(cores: float, expected: str):
    assert _cpu_max(cores) == expected


def test_cgroup_manager_writes_limits_then_removes(tmp_path):
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    cg = mgr.create("handle-1")
    assert (cg / "memory.max").read_text() == str(512 * 1024 * 1024)
    assert (cg / "cpu.max").read_text() == "100000 100000"
    assert (cg / "pids.max").read_text() == "256"

    mgr.remove(cg)
    # Best-effort: cgroup.kill is written so real cgroups reap their procs.
    assert (cg / "cgroup.kill").read_text() == "1"


def test_acl_argv_grants_uid_default_and_recursive_access():
    argv = _acl_argv(Path("/ws/root"), 100123)
    assert argv == [
        "setfacl",
        "-R",
        "-m",
        "u:100123:rwx",
        "-d",
        "-m",
        "u:100123:rwx",
        "/ws/root",
    ]


def test_setpriv_cgroup_argv_joins_cgroup_then_drops_privilege():
    argv = _setpriv_cgroup_argv(
        ["python3", "-c", "print(1)"], uid=100, gid=100, cgroup=Path("/cg/h1")
    )
    # A shell joins the cgroup (writes its own pid), then execs setpriv which
    # drops to the uid/gid; the command rides through `"$@"` (no re-quoting).
    assert argv[:3] == ["sh", "-c", 'echo $$ > /cg/h1/cgroup.procs; exec "$@"']
    assert "setpriv" in argv
    assert "--reuid=100" in argv and "--regid=100" in argv
    assert "--clear-groups" in argv
    assert argv[-3:] == ["python3", "-c", "print(1)"]


async def test_create_provisions_workspace_uid_cgroup_and_acl(isolated):
    h = await isolated.create(SandboxSpec())
    ws = isolated._workspace(h)
    st = ws.stat()
    assert st.st_uid == os.getuid()
    assert st.st_mode & 0o777 == 0o700
    ident = isolated._identities[h.id]
    assert ident.uid == os.getuid()
    assert (ident.cgroup / "memory.max").read_text() == str(64 * 1024 * 1024)
    assert (ident.cgroup / "pids.max").read_text() == "64"
    assert isolated.acl_calls == [_acl_argv(ws, os.getuid())]


async def test_create_provisions_per_sandbox_home_writable_by_uid(isolated):
    """#393: create must provision a per-sandbox `.home` (a workspace sibling,
    in the infra area) owned by the sandbox uid + 0700 — so the dropped uid can
    write the carrier launcher's HOME/caches and a `pip --user` install there,
    isolated from other sandboxes on the pod."""
    h = await isolated.create(SandboxSpec())
    home = isolated._require(h) / ".home"
    st = home.stat()
    assert st.st_uid == os.getuid()
    assert st.st_mode & 0o777 == 0o700


async def test_kill_frees_identity_and_reaps_cgroup(isolated):
    h = await isolated.create(SandboxSpec())
    ident = isolated._identities[h.id]
    await isolated.kill(h)
    assert h.id not in isolated._identities
    assert (ident.cgroup / "cgroup.kill").read_text() == "1"
    # uid was returned to the pool ⇒ a fresh sandbox reuses it
    h2 = await isolated.create(SandboxSpec())
    assert isolated._identities[h2.id].uid == os.getuid()


async def test_exec_argv_wraps_command_with_isolation(isolated):
    h = await isolated.create(SandboxSpec())
    argv, cwd, env = isolated._exec_argv(h, ["echo", "hi"])
    assert argv[0] == "sh" and "setpriv" in argv
    assert argv[-2:] == ["echo", "hi"]
    assert env["TMPDIR"] == str(cwd)  # per-handle tmp, no shared /tmp leak
    ident = isolated._identities[h.id]
    assert f"--reuid={ident.uid}" in argv


# ---- chown-on-write: app/host-written files must be owned by the sandbox uid (#504) ----


def _uid_of(isolated, h):
    return isolated._identities[h.id].uid


async def test_upload_chowns_file_to_sandbox_uid(isolated):
    h = await isolated.create(SandboxSpec())
    isolated.chown_calls.clear()  # ignore provisioning chowns; assert only this write's
    await isolated.upload(h, b"data", "brief.md")
    assert (isolated._workspace(h) / "brief.md", _uid_of(isolated, h)) in isolated.chown_calls


async def test_upload_file_chowns_file_to_sandbox_uid(isolated, tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    h = await isolated.create(SandboxSpec())
    isolated.chown_calls.clear()
    await isolated.upload_file(h, src, "data/out.bin")
    assert (isolated._workspace(h) / "data/out.bin", _uid_of(isolated, h)) in isolated.chown_calls


async def test_mkdir_chowns_dir_to_sandbox_uid(isolated):
    h = await isolated.create(SandboxSpec())
    isolated.chown_calls.clear()
    await isolated.mkdir(h, "outputs")
    assert (isolated._workspace(h) / "outputs", _uid_of(isolated, h)) in isolated.chown_calls


async def test_rename_chowns_destination_to_sandbox_uid(isolated):
    h = await isolated.create(SandboxSpec())
    await isolated.upload(h, b"x", "a.txt")
    isolated.chown_calls.clear()
    await isolated.rename(h, "a.txt", "sub/b.txt")
    assert (isolated._workspace(h) / "sub/b.txt", _uid_of(isolated, h)) in isolated.chown_calls


async def test_own_chowns_the_whole_created_parent_chain(isolated):
    h = await isolated.create(SandboxSpec())
    isolated.chown_calls.clear()
    await isolated.upload(h, b"x", "a/b/c.txt")
    ws = isolated._workspace(h)
    owned = {p for p, _ in isolated.chown_calls}
    assert {ws / "a", ws / "a/b", ws / "a/b/c.txt"} <= owned
    assert all(p == ws or ws in p.parents for p, _ in isolated.chown_calls)


async def test_own_never_chowns_a_path_outside_the_workspace(isolated, tmp_path):
    h = await isolated.create(SandboxSpec())
    isolated.chown_calls.clear()
    isolated._own(h, tmp_path / "elsewhere")
    assert isolated.chown_calls == []


def test_run_chown_calls_oschown_with_uid_and_unchanged_gid(monkeypatch, tmp_path):
    calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(os, "chown", lambda p, u, g: calls.append((p, u, g)))
    _run_chown(tmp_path / "f", 4321)
    assert calls == [(tmp_path / "f", 4321, -1)]


def test_constructs_default_chown_runner(tmp_path):
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "s", cgroup_root=tmp_path / "c", uid_min=100000, uid_max=100000
    )
    assert sb._chown_runner is _run_chown


def test_run_setfacl_invokes_subprocess():
    # The real ACL runner just shells out; exercise the call boundary with a
    # harmless command so the line is covered without depending on `setfacl`.
    _run_setfacl(["true"])
    with pytest.raises(subprocess.CalledProcessError):
        _run_setfacl(["false"])


async def test_kill_unknown_handle_has_no_identity_and_raises(isolated):
    from sandbox_host.protocol import SandboxHandle, SandboxNotFound

    # No identity recorded for an uncreated handle ⇒ skip cgroup/uid teardown,
    # then the inherited kill reports the unknown handle.
    with pytest.raises(SandboxNotFound):
        await isolated.kill(SandboxHandle(id="never-created"))


def test_constructs_default_acl_runner(tmp_path):
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "s",
        cgroup_root=tmp_path / "c",
        uid_min=100000,
        uid_max=100000,
    )
    assert sb._acl_runner is _run_setfacl
