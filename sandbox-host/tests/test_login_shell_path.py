"""The login-shell PATH guard, host-side.

This is the copy that PRODUCTION runs: the hosted sandbox has no userns jail
(uid + cgroup only, #393), so every agent `exec` goes down the unjailed branch,
and `bash -lc "python3 …"` — plus the `sh -lc` wrapper every workflow node
command rides — sources /etc/profile, which on Debian hard-resets PATH and drops
the per-sandbox `.jailbin` shim dir. The guard the images install reads that dir
back out of `SANDBOX_JAILBIN`; this pins the half that exports it.

The snippet's own behaviour and the images' COPY are pinned once, app-side, in
`tests/sandbox/test_login_shell_path.py` — the file is shared, only the exec
wiring is duplicated per copy.

Deliberately NOT `pytest.mark.integration`: the jail has had this guard since
#350, but only under a `@_needs_userns` test in an all-integration module, so CI
never ran it and the unjailed path went without for months. The lock belongs in
the set CI actually runs.
"""

from __future__ import annotations

import os
from pathlib import Path

from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.protocol import SandboxSpec


async def test_unjailed_exec_exports_the_shim_dir_the_login_guard_needs(tmp_path) -> None:
    """Asserted against `PATH`'s own first entry rather than a literal, so the
    exported variable and the directory actually prepended cannot drift into
    disagreeing — that drift would leave login shells pointed at a stale dir
    while direct execs kept working, the hardest version of this to notice."""
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec())
    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    assert env["SANDBOX_JAILBIN"] == env["PATH"].split(os.pathsep)[0]
    assert Path(env["SANDBOX_JAILBIN"]).name == ".jailbin"
