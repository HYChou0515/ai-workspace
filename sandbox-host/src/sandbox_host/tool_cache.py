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
import logging
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

logger = logging.getLogger(__name__)


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
        # `lchown`/`lstat`, never their following counterparts. The safe tar
        # filter permits a dangling RELATIVE symlink — it refuses absolute
        # paths and `..` escapes, not this — so following one means operating
        # on something that is not there, and the `FileNotFoundError` leaves
        # through `ensure`, which catches `TarError` and nothing else.
        os.lchown(path, 0, 0)
        if path.is_symlink():
            # A symlink's own mode is not consulted on Linux, and chmod
            # follows. There is nothing here to take away.
            continue
        os.chmod(path, path.lstat().st_mode & ~0o022)


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
            # `mkdtemp` gives 0700, which is right for a half-written tree and
            # wrong for an installed one: the processes that run a tool are
            # never this one. On the host a sandbox runs as an unprivileged
            # per-item uid; in the MCP runner the process drops to whoever
            # owns the workspace. Neither could enter a 0700 directory, and
            # the failure arrives as `PermissionError` on exec — indis-
            # tinguishable from a broken bundle.
            #
            # Set on the way in, before the rename, so nothing is ever visible
            # under its sha with the staging mode.
            staging.chmod(0o755)
            self._harden(staging)
            # Rename last, so a half-written tree is never visible under the
            # sha. A crash mid-unpack leaves a dot-prefixed directory to sweep,
            # never a bundle that looks installed but is missing files.
            try:
                staging.rename(installed)
            except OSError as exc:
                # Two sandboxes opening the same tool at once. Identical bytes
                # by construction — the sha is the name — so whoever landed
                # first installed exactly what this one would have.
                if not installed.is_dir():
                    raise
                logger.debug("bundle %s was installed by another caller (%s)", sha, exc)
                shutil.rmtree(staging, ignore_errors=True)
        except tarfile.TarError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ToolCacheError(f"bundle {sha} could not be unpacked: {exc}") from exc
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return installed

    def _size_of(self, path: Path) -> int:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    def sweep(self, *, in_use: set[str], max_bytes: int | None = None) -> list[str]:
        """Bring the cache under `max_bytes`, evicting unreferenced bundles
        oldest-first. Returns the shas removed.

        An unreferenced bundle is NOT rubbish — it is the version somebody may
        roll back to, and keeping it is what makes that a remount instead of a
        150MB download. So it stays until the disk actually needs the room.

        `in_use` is absolute. A bundle a sandbox has mounted is never evicted,
        however full the cache: an over-full cache is a capacity problem, and
        deleting a tool out from under a running turn is a correctness one. If
        everything left is in use and the cache is still over, that is a host
        that needs more disk, and it says so rather than breaking something.

        With no ceiling there is nothing to bound growth, so nothing
        unreferenced is kept."""
        if not self._root.is_dir():
            return []
        installed = [p for p in self._root.iterdir() if p.is_dir() and _SHA256.match(p.name)]
        # Oldest first: whatever has to go, goes in the order it stopped being
        # the version anyone was on.
        installed.sort(key=lambda p: p.stat().st_mtime)

        if max_bytes is None:
            removed = [p.name for p in installed if p.name not in in_use]
            for path in installed:
                if path.name not in in_use:
                    shutil.rmtree(path, ignore_errors=True)
            return removed

        total = sum(self._size_of(p) for p in installed)
        removed: list[str] = []
        for path in installed:
            if total <= max_bytes:
                break
            if path.name in in_use:
                continue
            total -= self._size_of(path)
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path.name)
        if total > max_bytes:
            logger.warning(
                "tool cache is %d bytes over its ceiling and every bundle left is in "
                "use — this host needs more disk, not a smaller cache",
                total - max_bytes,
            )
        return removed
