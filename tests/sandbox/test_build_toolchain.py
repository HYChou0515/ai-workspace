"""What a WUI build needs to exist inside a sandbox.

A page written with a bundler is built by the platform, on a person's click
(`api/wui_routes.py`). That is the ONE build this platform runs on a user's
behalf, and it needs two things the sandbox does not otherwise care about: the
package manager, and a PATH that can find it.

Both were missing, and both failed in the same unhelpful way — `sh: pnpm: not
found`, from a button that had just told the person a build was starting. Neither
could be caught by a test of the route: they are properties of the image and of
the jail's bootstrap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

# The app's own copy and the hosted sandbox's copy of the jail bootstrap. They
# are duplicated deliberately (the host ships separately); a change to one that
# misses the other is the recurring defect this pins.
_BOOTSTRAPS = (
    "src/workspace_app/sandbox/local_process.py",
    "sandbox-host/src/sandbox_host/local_process.py",
)


@pytest.mark.parametrize("module", _BOOTSTRAPS)
def test_the_jail_can_reach_a_globally_installed_binary(module: str) -> None:
    """`/usr/local/bin` is where `npm install -g` puts a command on Debian — it
    is how the images install pptxgenjs and pnpm — and it is on the PATH of
    every ordinary Debian shell. The jail's bootstrap listed the system dirs and
    left it out, so anything the image installed globally was invisible: present
    on disk, unreachable by name.

    Appended last on purpose. Nothing that resolves today changes position; only
    names that resolved to nothing at all start resolving."""
    text = (_REPO / module).read_text()
    line = re.search(r'^export PATH="(.+)"$', text, re.MULTILINE)
    assert line, f"{module}: the jail bootstrap no longer exports a PATH"
    entries = line.group(1).split(":")
    assert "/usr/local/bin" in entries, f"{module}: {entries}"
    assert entries[0] == "/tmp/.jailbin", (
        f"{module}: the shim dir must stay FIRST — it exists to shadow the "
        f"image's own interpreter (#350), and it cannot do that from {entries}"
    )


def test_the_sandbox_image_ships_the_package_manager_a_build_needs() -> None:
    """Debian's `nodejs` brings node and npm, and not pnpm. Without this line
    every Rebuild ends at `sh: pnpm: not found` — which is exactly what the
    first one pressed against a real sandbox did."""
    text = (_REPO / "sandbox-host/Dockerfile").read_text()

    assert re.search(r"^RUN npm install -g pnpm", text, re.MULTILINE), (
        "sandbox-host/Dockerfile installs no pnpm; the WUI build route runs it"
    )
