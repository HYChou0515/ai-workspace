"""`SANDBOX_HOME` must outlive a single `exec`, in both exec paths.

#393 gave the carrier launcher a per-sandbox `HOME` so a `pip --user` install
stays private to one sandbox. Unjailed it points at `<root>/.home`, a workspace
sibling that lives and dies with the sandbox. The JAIL branch pointed at `/tmp`
instead — and the jail bootstrap mounts a **fresh tmpfs over /tmp on every
exec**. So a jailed install did not merely fail to persist across a recycle; it
did not survive to the NEXT COMMAND:

    exec(["pip", "install", "cowsay"])       -> Successfully installed cowsay
    exec(["python", "-c", "import cowsay"])  -> ModuleNotFoundError

which is the only way anyone would ever use it. That was invisible while the
bundled interpreter still carried its PEP 668 marker, because pip refused the
install outright and refused it loudly; removing the marker turned a loud
refusal into a silent evaporation.

Both paths now name the sandbox's own `.home`. This is NOT persistence: it is
reaped with the sandbox exactly as before, and an install still does not survive
a recycle. It is the jail catching up to what unjailed already did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxSpec

from .test_local_process import _needs_userns


async def test_the_jail_puts_home_on_the_sandbox_not_on_the_per_exec_tmpfs(tmp_path) -> None:
    """`/.home` is the chroot-relative spelling of the same `<root>/.home` the
    unjailed path uses — a sibling of the `/root` workspace, in the infra area,
    so it is never walked, synced or shown in the file tree, and it is removed
    with the sandbox."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True)
    h = await sb.create(SandboxSpec())
    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    assert env["SANDBOX_HOME"] == "/.home"
    assert (Path(_cwd) / ".home").is_dir()  # and it really exists to be written to


@_needs_userns
@pytest.mark.integration
async def test_what_a_jailed_exec_writes_to_home_is_there_for_the_next_one(tmp_path) -> None:
    """The behaviour the unit test stands in for, and the one that actually
    broke: two consecutive execs, seconds apart, in the same sandbox."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=True)
    h = await sb.create(SandboxSpec())
    first = await sb.exec(h, ["sh", "-c", 'echo kept > "$SANDBOX_HOME/marker"'])
    assert first.exit_code == 0, first.stderr
    second = await sb.exec(h, ["sh", "-c", 'cat "$SANDBOX_HOME/marker"'])
    assert second.exit_code == 0, second.stderr
    assert second.stdout.decode().strip() == "kept"


async def test_a_plain_exec_gets_home_off_the_synced_workspace(tmp_path) -> None:
    """HOME for ANY exec — not just the carrier launcher — is the per-sandbox
    `.home`, never the workspace.

    A tool that writes its profile to ``$HOME`` must not land it in the workspace:
    there it is mirrored to (possibly NFS) durable and persists across turns, and
    LibreOffice — the tool that exposed this — then cannot create/lock its user
    profile and aborts with "User installation could not be completed", so
    ``soffice --convert-to pdf`` produces nothing in a sandbox while the KB parser
    (app process, HOME=/root) converts the same deck fine. cwd stays the workspace
    (the user's files); HOME is the config/cache home, a workspace sibling that is
    never walked or synced. Completes #393, which moved only the carrier's HOME.
    """
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec(), sandbox_id="pinned")
    _argv, cwd, env = sb._exec_argv(h, ["true"])
    home = Path(env["HOME"])
    assert home == Path(cwd).parent / ".home"  # the infra-area sibling
    assert home != Path(cwd)  # NOT the mirrored workspace
    assert home.is_dir()  # provisioned + writable by the exec
