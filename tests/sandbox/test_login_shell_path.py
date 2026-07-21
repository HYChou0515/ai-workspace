"""The login-shell PATH guard for sandboxes that run WITHOUT the userns jail.

The agent commonly runs commands as `bash -lc "python3 -c …"`, and the workflow
runner wraps every node command as `sh -lc "export WF_TOKEN=…; <cmd>"`. The `-l`
makes the shell source `/etc/profile`, which on Debian/Ubuntu **hard-resets**
PATH — dropping the sandbox's `.jailbin` shim dir and routing `python`/`python3`
back to whatever interpreter the image happens to ship. That interpreter has
none of the python-stack carrier's deps, and none of the carrier launcher's
`HOME` rewriting, so a `pip --user` install there lands somewhere the carrier
will never read.

The jail path already fixed this by overlaying a tmpfs on `/etc/profile.d` and
dropping a script that re-prepends the shim dir (see `_JAIL_BOOTSTRAP`). The
UNJAILED path — which is what production runs, since the hosted sandbox has no
userns jail (uid + cgroup only, #393) — never got the same guard: it only
exported PATH, which is exactly what `/etc/profile` throws away.

Unjailed there is no chroot to overlay, so the guard ships as a real file that
the sandbox images install into `/etc/profile.d/`. It reads the shim dir from
`SANDBOX_JAILBIN`, exported per-exec, because the dir is per-sandbox and a
pod-wide file cannot hardcode it. `/etc/profile` only resets PATH — exported
variables survive it, which is what makes this work.

These tests pin the snippet's own behaviour; `test_local_process.py` pins that
the exec path exports `SANDBOX_JAILBIN`, and `test_sandbox_images.py` pins that
the images actually install the snippet.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxSpec

# Deliberately NOT `pytest.mark.integration`: the jail's own login-shell guard
# has been covered since #350, but only by a `@_needs_userns` test inside an
# all-integration module — so CI never ran it, and the unjailed path could ship
# without the same guard for months. These are plain unit tests so the lock is
# in the set CI actually runs.

SNIPPET = Path(__file__).resolve().parents[2] / "docker" / "profile.d" / "sandbox-jailbin.sh"

# What Debian's /etc/profile hard-resets PATH to for a non-root login shell,
# right before it sources /etc/profile.d/*.sh. This is the clobber we survive.
_PROFILE_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games"


def _path_after_snippet(sandbox_jailbin: str | None) -> str:
    """PATH as a login shell would see it: profile's reset, then our snippet."""
    env = {"PATH": "/usr/bin:/bin"}
    if sandbox_jailbin is not None:
        env["SANDBOX_JAILBIN"] = sandbox_jailbin
    proc = subprocess.run(
        ["sh", "-c", f'PATH={_PROFILE_PATH}; . "{SNIPPET}"; printf "%s" "$PATH"'],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return proc.stdout


def test_the_shim_dir_leads_path_again_after_profile_reset() -> None:
    """The whole point: a login shell must still find the carrier's `python`."""
    assert _path_after_snippet("/sb/item/.jailbin").split(":")[0] == "/sb/item/.jailbin"


def test_a_shell_with_no_sandbox_behind_it_is_left_exactly_alone() -> None:
    """The snippet is installed image-wide, so it runs in every login shell on
    the pod — including the host service's own, which has no sandbox and no
    SANDBOX_JAILBIN. It must then be a complete no-op.

    Specifically it must not prepend an EMPTY entry: an empty element in PATH
    means "the current directory", so a careless `PATH="$VAR:$PATH"` on an unset
    variable would silently make every login shell on the pod execute binaries
    out of whatever directory it happens to be sitting in."""
    assert _path_after_snippet(None) == _PROFILE_PATH


async def test_unjailed_exec_exports_the_shim_dir_the_snippet_needs(tmp_path) -> None:
    """The snippet can only restore what the exec path names for it.

    Asserted against `PATH`'s own first entry rather than a literal, so the
    exported variable and the directory actually prepended cannot drift into
    disagreeing — a silent drift here would leave login shells pointed at a
    stale or empty dir while direct execs kept working, which is the hardest
    version of this bug to notice.
    """
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec(), sandbox_id="pinned")
    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    assert env["SANDBOX_JAILBIN"] == env["PATH"].split(os.pathsep)[0]
    assert Path(env["SANDBOX_JAILBIN"]).name == ".jailbin"


# Every image that runs an UNJAILED sandbox exec must install the snippet: the
# hosted sandbox-host (production, `kind: http`) and the app image itself, which
# runs LocalProcessSandbox in-process for `kind: local`. `Dockerfile.workspace`
# is the deprecated docker-sandbox image and is deliberately not covered.
_EXEC_IMAGES = ("sandbox-host/Dockerfile", "docker/Dockerfile")


@pytest.mark.parametrize("dockerfile", _EXEC_IMAGES)
def test_every_image_that_execs_installs_the_login_shell_guard(dockerfile: str) -> None:
    """Without the COPY the whole mechanism is inert: the exec path would keep
    exporting SANDBOX_JAILBIN to a shell that has nothing to read it."""
    text = (Path(__file__).resolve().parents[2] / dockerfile).read_text()
    assert re.search(
        r"^COPY\s+docker/profile\.d/sandbox-jailbin\.sh\s+/etc/profile\.d/",
        text,
        re.MULTILINE,
    ), f"{dockerfile} does not install docker/profile.d/sandbox-jailbin.sh"
