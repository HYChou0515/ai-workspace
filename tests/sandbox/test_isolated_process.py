"""IsolatedProcessSandbox — unit tests (#345), non-root, no real isolation.

Every privileged operation is seamed so it runs as the dev user: the uid is
DERIVED from the item id, but the fixture pins ``uid_base=getuid`` + ``uid_range=1``
so the derivation collapses to the test's own uid (``chown`` to self works
non-root); cgroup writes target an injected tmp dir (identical plain-file writes);
and the one system-binary boundary (``setfacl``) is captured instead of shelled
out. Real privilege-drop / cgroup enforcement lives in the root-gated integration
test (``test_isolated_process_integration.py``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from workspace_app.sandbox.isolated_process import (
    IsolatedProcessSandbox,
    _acl_argv,
    _CgroupManager,
    _cpu_max,
    _derive_uid,
    _detect_cgroup_root,
    _has_cap_setuid,
    _parse_size,
    _read_cap_eff,
    _run_chown,
    _run_setfacl,
    _setpriv_cgroup_argv,
    isolation_supported,
)
from workspace_app.sandbox.protocol import SandboxHandle, SandboxNotFound, SandboxSpec


@pytest.fixture
def isolated(tmp_path):
    """A sandbox whose derived uid collapses to the test's own uid (uid_range=1 ⇒
    hash%1==0 ⇒ uid==uid_base==getuid, so `chown` is a non-root no-op), whose
    cgroup root is a tmp tree, and whose ACL application is captured."""
    calls: list[list[str]] = []
    chowns: list[tuple[Path, int]] = []
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        uid_base=os.getuid(),
        uid_range=1,
        memory_max="64M",
        cpu_cores=0.5,
        pids_max=64,
        acl_runner=calls.append,
        chown_runner=lambda p, u: chowns.append((p, u)),
    )
    sb.acl_calls = calls  # ty: ignore[unresolved-attribute]
    sb.chown_calls = chowns  # ty: ignore[unresolved-attribute]
    return sb


# ---- pure helpers ----


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


def test_derive_uid_is_stable_and_in_range():
    a = _derive_uid("item-1", uid_base=1_000_000, uid_range=2_000_000_000)
    again = _derive_uid("item-1", uid_base=1_000_000, uid_range=2_000_000_000)
    other = _derive_uid("item-2", uid_base=1_000_000, uid_range=2_000_000_000)
    assert a == again  # pure function of the id → same uid on every pod
    assert a != other  # different items → (almost surely) different uids
    assert 1_000_000 <= a < 1_000_000 + 2_000_000_000


def test_derive_uid_collapses_to_base_when_range_one():
    # uid_range=1 ⇒ hash % 1 == 0 ⇒ uid == uid_base (the non-root test trick).
    assert _derive_uid("anything", uid_base=4321, uid_range=1) == 4321


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
    assert argv[:3] == ["sh", "-c", 'echo $$ > /cg/h1/cgroup.procs; exec "$@"']
    assert "setpriv" in argv
    assert "--reuid=100" in argv and "--regid=100" in argv
    assert "--clear-groups" in argv
    assert argv[-3:] == ["python3", "-c", "print(1)"]


# ---- capability / support detection ----


@pytest.mark.parametrize(
    ("cap_eff", "expected"),
    [
        ("00000000000000ff", True),  # low 8 bits set ⇒ includes CAP_SETUID (bit 7)
        ("0000000000000080", True),  # exactly CAP_SETUID
        ("000000000000007f", False),  # bits 0-6 only, NOT bit 7
        ("not-hex", False),  # malformed ⇒ fail closed
    ],
)
def test_has_cap_setuid(cap_eff: str, expected: bool):
    assert _has_cap_setuid(cap_eff) is expected


def test_isolation_supported_no_cap(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr("workspace_app.sandbox.isolated_process._read_cap_eff", lambda: "0")
    ok, reason = isolation_supported(None)
    assert ok is False and "CAP_SETUID" in reason


def test_isolation_supported_no_cgroup_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)  # root ⇒ cap check passes
    monkeypatch.setattr("workspace_app.sandbox.isolated_process._detect_cgroup_root", lambda: None)
    ok, reason = isolation_supported(None)
    assert ok is False and "cgroup" in reason


def test_isolation_supported_cgroup_not_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    ok, reason = isolation_supported(str(tmp_path / "does-not-exist"))
    assert ok is False and "writable" in reason


def test_isolation_supported_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    ok, reason = isolation_supported(str(tmp_path))  # a real writable dir
    assert ok is True and reason == "ok"


def test_read_cap_eff_returns_a_hex_string():
    # On Linux this reads the real CapEff; the value is host-dependent but must be
    # parseable hex (covers the found-line branch).
    assert int(_read_cap_eff(), 16) >= 0


def test_read_cap_eff_defaults_to_zero_when_unreadable(monkeypatch):
    def _boom(self, *a, **k):
        raise OSError("no /proc")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert _read_cap_eff() == "0"


def test_read_cap_eff_defaults_to_zero_when_no_capeff_line(monkeypatch):
    # A status file without a CapEff: line ⇒ the loop falls through to "0".
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: "Name:\tpython\nPid:\t1\n")
    assert _read_cap_eff() == "0"


def test_detect_cgroup_root_when_present_or_absent():
    # Either the host has a v2 root (a Path) or it doesn't (None) — both are valid
    # and exercise the predicate without assuming the test box's layout.
    result = _detect_cgroup_root()
    assert result is None or isinstance(result, Path)


# ---- cgroup manager ----


def test_cgroup_manager_writes_limits_then_removes(tmp_path):
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    cg = mgr.create("handle-1")
    assert (cg / "memory.max").read_text() == str(512 * 1024 * 1024)
    assert (cg / "cpu.max").read_text() == "100000 100000"
    assert (cg / "pids.max").read_text() == "256"

    mgr.remove(cg)
    assert (cg / "cgroup.kill").read_text() == "1"


def test_cgroup_manager_create_is_idempotent(tmp_path):
    # #345: a re-create on the same pod (item whose local session was dropped but
    # whose shared dir stayed live) must re-attach, not raise.
    mgr = _CgroupManager(tmp_path, memory_max="64M", cpu_cores=1.0, pids_max=64)
    cg1 = mgr.create("h")
    cg2 = mgr.create("h")  # again — no FileExistsError
    assert cg1 == cg2
    assert (cg2 / "memory.max").read_text() == str(64 * 1024 * 1024)


# ---- sandbox lifecycle ----


async def test_create_provisions_workspace_uid_cgroup_and_acl(isolated):
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    ws = isolated._workspace(h)
    st = ws.stat()
    assert st.st_uid == os.getuid()
    assert st.st_mode & 0o777 == 0o700
    cg = isolated._cgroup_root / h.id
    assert (cg / "memory.max").read_text() == str(64 * 1024 * 1024)
    assert (cg / "pids.max").read_text() == "64"
    assert isolated.acl_calls == [_acl_argv(ws, os.getuid())]


async def test_create_provisions_per_sandbox_home_writable_by_uid(isolated):
    """#393: create must provision a per-sandbox `.home` (a workspace sibling,
    in the infra area) owned by the item uid + 0700 — so the dropped uid can
    write the carrier launcher's HOME/caches and a `pip --user` install there,
    isolated from other sandboxes on the pod."""
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    home = isolated._require(h) / ".home"
    st = home.stat()
    assert st.st_uid == os.getuid()
    assert st.st_mode & 0o777 == 0o700


async def test_create_is_idempotent_for_a_shared_item(isolated):
    # Re-creating the same item id re-attaches (no raise) — the shared-dir model.
    h1 = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    h2 = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    assert h1.id == h2.id


async def test_kill_reaps_cgroup_and_removes_dir(isolated):
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    cg = isolated._cgroup_root / h.id
    await isolated.kill(h)
    assert (cg / "cgroup.kill").read_text() == "1"  # reaped
    with pytest.raises(SandboxNotFound):
        isolated._require(h)  # dir gone


async def test_kill_unknown_handle_raises(isolated):
    with pytest.raises(SandboxNotFound):
        await isolated.kill(SandboxHandle(id="never-created"))


async def test_exec_argv_wraps_command_with_isolation(isolated):
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    argv, cwd, env = isolated._exec_argv(h, ["echo", "hi"])
    assert argv[0] == "sh" and "setpriv" in argv
    assert argv[-2:] == ["echo", "hi"]
    assert env["TMPDIR"] == str(cwd)  # per-item tmp, no shared /tmp leak
    assert f"--reuid={isolated._uid_for(h.id)}" in argv


# ---- chown-on-write: app/host-written files must be owned by the item uid (#504) ----


async def test_upload_chowns_file_to_item_uid(isolated):
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    isolated.chown_calls.clear()  # ignore provisioning chowns; assert only this write's
    await isolated.upload(h, b"data", "brief.md")
    ws = isolated._workspace(h)
    uid = isolated._uid_for(h.id)
    assert (ws / "brief.md", uid) in isolated.chown_calls


async def test_upload_file_chowns_file_to_item_uid(isolated, tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")  # the staged restore/upload source
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    isolated.chown_calls.clear()
    await isolated.upload_file(h, src, "data/out.bin")
    ws = isolated._workspace(h)
    uid = isolated._uid_for(h.id)
    assert (ws / "data/out.bin", uid) in isolated.chown_calls


async def test_mkdir_chowns_dir_to_item_uid(isolated):
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    isolated.chown_calls.clear()
    await isolated.mkdir(h, "outputs")
    ws = isolated._workspace(h)
    uid = isolated._uid_for(h.id)
    assert (ws / "outputs", uid) in isolated.chown_calls


async def test_rename_chowns_destination_to_item_uid(isolated):
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    await isolated.upload(h, b"x", "a.txt")
    isolated.chown_calls.clear()
    await isolated.rename(h, "a.txt", "sub/b.txt")
    ws = isolated._workspace(h)
    uid = isolated._uid_for(h.id)
    assert (ws / "sub/b.txt", uid) in isolated.chown_calls


async def test_own_chowns_the_whole_created_parent_chain(isolated):
    # A nested write must leave EVERY new ancestor uid-owned, not just the leaf —
    # a mid-chain root-owned dir still breaks git / rmdir.
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    isolated.chown_calls.clear()
    await isolated.upload(h, b"x", "a/b/c.txt")
    ws = isolated._workspace(h)
    owned = {p for p, _ in isolated.chown_calls}
    assert {ws / "a", ws / "a/b", ws / "a/b/c.txt"} <= owned
    # never chown above the workspace root (infra siblings / shared root)
    assert all(p == ws or ws in p.parents for p, _ in isolated.chown_calls)


async def test_own_never_chowns_a_path_outside_the_workspace(isolated, tmp_path):
    # Defensive: a target that isn't under the workspace (a path-escape) must
    # chown NOTHING — never the infra siblings or anything above the ws root.
    h = await isolated.create(SandboxSpec(), sandbox_id="item-1")
    isolated.chown_calls.clear()
    isolated._own(h, tmp_path / "elsewhere")
    assert isolated.chown_calls == []


# ---- the system-binary boundary ----


def test_run_setfacl_invokes_subprocess():
    _run_setfacl(["true"])
    with pytest.raises(subprocess.CalledProcessError):
        _run_setfacl(["false"])


def test_constructs_default_acl_runner(tmp_path):
    sb = IsolatedProcessSandbox(root_dir=tmp_path / "s", cgroup_root=tmp_path / "c")
    assert sb._acl_runner is _run_setfacl


def test_run_chown_calls_oschown_with_uid_and_unchanged_gid(monkeypatch, tmp_path):
    calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(os, "chown", lambda p, u, g: calls.append((p, u, g)))
    _run_chown(tmp_path / "f", 4321)
    assert calls == [(tmp_path / "f", 4321, -1)]  # gid -1 = leave as-is


def test_constructs_default_chown_runner(tmp_path):
    sb = IsolatedProcessSandbox(root_dir=tmp_path / "s", cgroup_root=tmp_path / "c")
    assert sb._chown_runner is _run_chown
