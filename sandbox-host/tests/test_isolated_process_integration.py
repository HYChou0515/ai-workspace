"""IsolatedProcessSandbox — L3 integration: REAL isolation behaviour.

Validates what the L2 unit tests can only assert structurally: that `setpriv`
actually drops to the allocated uid, that one sandbox cannot read another's
files, and that the default ACL lets a sandbox modify a file the root host
wrote. Needs root (to setuid/chown to a foreign uid) + `setpriv` + `setfacl`, so
it is `integration`-tagged and skipped everywhere else — its lines are already
covered by the non-root unit suite; this only proves real-world behaviour.

cgroup_root points at a plain tmp dir: the cgroup-join `echo $$ > cgroup.procs`
then writes a harmless regular file, so the privilege-drop path is exercised
without provisioning a delegated cgroup hierarchy in the test.
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


@pytest.fixture
def isolated(tmp_path):
    return IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        uid_min=100000,
        uid_max=100010,
        memory_max="128M",
        cpu_cores=1.0,
        pids_max=128,
    )


@requires_isolation
async def test_exec_drops_to_allocated_uid(isolated):
    h = await isolated.create(SandboxSpec())
    result = await isolated.exec(h, ["id", "-u"])
    assert result.exit_code == 0
    assert result.stdout.strip() == str(isolated._identities[h.id].uid).encode()


@requires_isolation
async def test_sandboxes_cannot_read_each_others_files(isolated):
    a = await isolated.create(SandboxSpec())
    b = await isolated.create(SandboxSpec())
    await isolated.upload(a, b"top secret", "/secret.txt")
    a_secret = isolated._workspace(a) / "secret.txt"
    # B runs as a different uid; A's workspace is chmod 700 owned by A's uid.
    result = await isolated.exec(b, ["cat", str(a_secret)])
    assert result.exit_code != 0  # permission denied


@requires_isolation
async def test_sandbox_can_modify_root_uploaded_file_via_default_acl(isolated):
    h = await isolated.create(SandboxSpec())
    await isolated.upload(h, b"data\n", "/f.txt")  # created by the root host
    # The default ACL grants the sandbox uid rwx, so its own process can append.
    result = await isolated.exec(h, ["sh", "-c", "echo more >> f.txt && cat f.txt"])
    assert result.exit_code == 0
    assert b"more" in result.stdout
