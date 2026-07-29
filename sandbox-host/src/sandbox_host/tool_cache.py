"""Host-local, content-addressed store for third-party tool bundles (#674).

Why local, when the workspaces themselves live on shared storage: the
invariant that protects a tool from the sandbox running it is `root:root 755`
on a real filesystem. NFS cannot hold that (root_squash, inconsistent
ownership semantics across pods), so the shared layer only ever carries BYTES
— self-verifying by sha — and the runnable tree is unpacked locally, on every
host, where the kernel enforces the permissions.

Keyed by sha rather than by tool name, which buys three things at once:
the same bytes are unpacked once per host no matter how many sandboxes want
them; two sandboxes can run different versions of one tool without a fight;
and rolling back is just mounting a directory that is still here.

Everything below unpacks a stranger's tarball. It is therefore written as if
the tarball is hostile, because one day it will be.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path

#: The tools root holds two trees side by side: `builtin/` is baked into this
#: image and ships with the platform; `ext/` holds third-party bundles keyed by
#: sha. A sandbox is never shown this layout — it sees `/.tools/<name>` and
#: nothing else, so no tool path in any prompt or bundle has to know about it.
BUILTIN_DIR = "builtin"
EXT_DIR = "ext"

#: A sha256 hex digest and nothing else. This is not a style check: the sha
#: arrives inside a manifest published by whoever controls the artifact URL,
#: and it is about to be used as a directory name.
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# A seam for the privileged hardening step: the default chowns to root and
# strips group/other write, which needs root; tests inject a spy so the rest
# of the behaviour is exercised unprivileged. Mirrors `ChownRunner` in
# isolated_process.py.
Hardener = Callable[[Path], None]


class ToolCacheError(Exception):
    """The bundle cannot be installed — bad sha, or a tarball that tried to
    write somewhere it has no business writing."""


def _harden(root: Path) -> None:
    """Make the installed tree root-owned and unwritable by anyone else.

    This is the whole protection: a sandbox's processes run as an unprivileged
    per-item uid, and the tools are mounted read-only, but the tree they are
    mounted FROM must not be writable by that uid either — otherwise one
    sandbox could rewrite a tool that every other sandbox on the host runs."""
    for path in [root, *root.rglob("*")]:
        os.chown(path, 0, 0)
        mode = path.stat().st_mode
        os.chmod(path, mode & ~0o022)


class ToolCache:
    """Bundles installed on this host, addressed by the sha of their bytes."""

    def __init__(self, root: Path, *, harden: Hardener = _harden) -> None:
        self._root = root
        self._harden = harden

    def path_for(self, sha: str) -> Path:
        """Where a bundle with this sha lives, installed or not."""
        if not _SHA256.match(sha):
            raise ToolCacheError(
                f"{sha!r} is not a sha256 digest — a manifest cannot be allowed to "
                "choose a path on this host"
            )
        return self._root / sha

    def has(self, sha: str) -> bool:
        return self.path_for(sha).is_dir()

    def ensure(self, sha: str, data: bytes) -> Path:
        """Install these bytes under their sha, and return the runnable tree.

        A no-op when that sha is already installed: identical bytes, so the
        second caller pays a stat instead of an unpack."""
        installed = self.path_for(sha)
        if installed.is_dir():
            return installed

        self._root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=self._root, prefix=f".{sha}."))
        try:
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                # `data` refuses absolute paths, `..` escapes, links pointing
                # outside the tree, devices and setuid bits. Refusing is right:
                # a bundle that needs any of those is not a bundle.
                tar.extractall(staging, filter="data")
            self._harden(staging)
            # Rename last, so a half-written tree is never visible under the
            # sha. A crash mid-unpack leaves a dot-prefixed directory to sweep,
            # never a bundle that looks installed but is missing files.
            staging.rename(installed)
        except tarfile.TarError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ToolCacheError(f"bundle {sha} could not be unpacked: {exc}") from exc
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return installed
