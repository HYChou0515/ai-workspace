"""The host opens a tool's sealed credentials for that tool's process only
(docs/plan-tool-identity-secrets.md P4).

The private key is a FILE (a mounted k8s secret), never an environment
variable: every sandbox command inherits this service's environment, so a key
there would be handed to every sandbox on the host.
"""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from sandbox_host.config import SandboxHostSettings, load_settings
from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.protocol import SandboxSpec
from sandbox_host.service import build_sandbox
from sandbox_host.tool_cache import EXT_DIR, ToolCache
from sandbox_host.tool_secrets import (
    SECRETS_FILE,
    private_key_from_text,
    public_key_text,
    seal,
)

# Sealed by the APP's copy of `tool_secrets` (cryptography 48, the builder's
# side) and pinned here, where cryptography 50 opens it: the two halves run on
# different versions of the library, and this is the proof they interoperate.
_PINNED_KEY = "2Jq7+DmHzU8+NdZFBt6aSjPGvl6YtDaGhJ1EAghY9nI="
_PINNED_SEALED = (
    '{\n  "format": 1,\n  "recipients": {\n    "8392770f6ac3451c": {\n'
    '      "MACHINE_PASSWORD": '
    '"njJT6RLW+oyqA4coGLWEWYvX5UnslpEehU4CA8GmBlRKQEFt0esiROuYZYG58bBFAHt/L+XudA=="\n'
    "    }\n  }\n}\n"
)

_PRINT = "import os; print(os.environ.get('MACHINE_PASSWORD') == 'hunter2')"


def _tool(code: str) -> list[str]:
    return ["../.tools/erp/launch", "run", code]


def _bundle(sealed: bytes | None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # The launcher runs its ARGUMENTS as Python, so a test can probe
        # through the one argv shape an `as_tool` exec may take.
        files = {"launch": f'#!/bin/sh\nexec {sys.executable} -c "$2"\n'.encode()}
        if sealed is not None:
            files[SECRETS_FILE] = sealed
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o755 if name == "launch" else 0o644
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


async def _sandbox(tmp_path: Path, sealed: bytes | None, key: X25519PrivateKey | None):
    tools = tmp_path / "tools"
    sha = "d" * 64
    ToolCache(tools / EXT_DIR, harden=lambda _p: None).ensure(sha, _bundle(sealed))
    sb = LocalProcessSandbox(
        root_dir=tmp_path / "sb", isolate=False, tools_dir=tools, tool_secret_key=key
    )
    handle = await sb.create(SandboxSpec(tools={"erp": sha}))
    return sb, handle


async def test_the_tool_gets_its_credential_and_nobody_sees_it(tmp_path):
    key = X25519PrivateKey.generate()
    sealed = seal({"MACHINE_PASSWORD": "hunter2"}, [key.public_key()])
    sb, h = await _sandbox(tmp_path, sealed, key)
    probe = _tool(_PRINT + "; print(os.environ['MACHINE_PASSWORD'])")
    result = await sb.exec(h, probe, as_tool="erp")
    assert result.exit_code == 0, result.stderr
    assert result.stdout == b"True\n***\n"


async def test_the_agents_own_command_gets_no_credential(tmp_path):
    key = X25519PrivateKey.generate()
    sealed = seal({"MACHINE_PASSWORD": "hunter2"}, [key.public_key()])
    sb, h = await _sandbox(tmp_path, sealed, key)
    result = await sb.exec(h, [sys.executable, "-c", _PRINT])
    assert result.stdout == b"False\n"


async def test_a_tool_without_sealed_credentials_runs_as_before(tmp_path):
    sb, h = await _sandbox(tmp_path, None, None)
    result = await sb.exec(h, _tool(_PRINT), as_tool="erp")
    assert result.exit_code == 0
    assert result.stdout == b"False\n"


async def test_a_host_with_no_key_refuses_and_says_who_has_to_act(tmp_path):
    """Running the tool without the value would end in "MACHINE_PASSWORD is not
    set" — and a card asking the USER for the machine password."""
    other = X25519PrivateKey.generate()
    sealed = seal({"MACHINE_PASSWORD": "hunter2"}, [other.public_key()])
    sb, h = await _sandbox(tmp_path, sealed, None)
    result = await sb.exec(h, _tool(_PRINT), as_tool="erp")
    assert result.exit_code == 3
    assert b"platform team" in result.stderr
    assert b"cannot set" in result.stderr
    assert result.stdout == b""


async def test_credentials_sealed_for_another_deployment_are_refused(tmp_path):
    ours, theirs = X25519PrivateKey.generate(), X25519PrivateKey.generate()
    sealed = seal({"MACHINE_PASSWORD": "hunter2"}, [theirs.public_key()])
    sb, h = await _sandbox(tmp_path, sealed, ours)
    result = await sb.exec(h, _tool(_PRINT), as_tool="erp")
    assert result.exit_code == 3
    assert b"not sealed for this deployment" in result.stderr


async def test_a_value_sealed_by_the_builders_library_opens_here(tmp_path):
    sb, h = await _sandbox(tmp_path, _PINNED_SEALED.encode(), private_key_from_text(_PINNED_KEY))
    result = await sb.exec(h, _tool(_PRINT), as_tool="erp")
    assert result.exit_code == 0, result.stderr
    assert result.stdout == b"True\n"


@pytest.mark.parametrize("name", ["../erp", "a/b", ".."])
async def test_a_tool_name_cannot_walk_out_of_the_view(tmp_path, name):
    key = X25519PrivateKey.generate()
    sb, h = await _sandbox(tmp_path, None, key)
    result = await sb.exec(h, [f"../.tools/{name}/launch", "run", "{}"], as_tool=name)
    assert result.exit_code == 126


# ── the key is a file the operator mounts ────────────────────────────────────


def test_the_key_file_is_a_setting(tmp_path):
    settings = load_settings({"SANDBOX_HOST_TOOL_SECRET_KEY_FILE": str(tmp_path / "k")})
    assert settings.tool_secret_key_file == str(tmp_path / "k")


def test_the_production_sandbox_holds_the_key_from_the_file(tmp_path):
    key = X25519PrivateKey.generate()
    import base64

    from cryptography.hazmat.primitives import serialization

    raw = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    path = tmp_path / "tool-secret.key"
    path.write_text(base64.b64encode(raw).decode() + "\n")
    sb = build_sandbox(
        SandboxHostSettings(cgroup_root=str(tmp_path / "cg"), tool_secret_key_file=str(path))
    )
    assert sb._tool_secret_key is not None
    assert public_key_text(sb._tool_secret_key.public_key()) == public_key_text(key.public_key())


def test_an_unreadable_key_file_stops_the_host_at_startup(tmp_path):
    """Starting without it would refuse every sealed tool at its first call —
    much later, in front of a user. Fail where the operator is looking."""
    with pytest.raises(Exception, match="tool secret key"):
        build_sandbox(
            SandboxHostSettings(
                cgroup_root=str(tmp_path / "cg"), tool_secret_key_file=str(tmp_path / "missing")
            )
        )


def test_no_key_file_means_no_key(tmp_path):
    sb = build_sandbox(SandboxHostSettings(cgroup_root=str(tmp_path / "cg")))
    assert sb._tool_secret_key is None


async def test_the_tools_own_credential_wins_over_a_user_variable_of_its_name(tmp_path):
    """A user who sets MACHINE_PASSWORD themselves must not replace the author's
    credential — nor be told, by the tool working, what it was."""
    key = X25519PrivateKey.generate()
    sealed = seal({"MACHINE_PASSWORD": "hunter2"}, [key.public_key()])
    sb, h = await _sandbox(tmp_path, sealed, key)
    result = await sb.exec(h, _tool(_PRINT), env={"MACHINE_PASSWORD": "mine"}, as_tool="erp")
    assert result.stdout == b"True\n"
