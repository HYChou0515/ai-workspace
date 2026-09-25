"""A view plugin's sandbox half: getting its bundle to `/.tools` (#847/#848 P6).

Where the bundle comes from depends on the sandbox backend:

- ``kind: local`` — boot copies each ``{bundle: …}`` into a MERGED tools root
  beside the prebuilt packages (``merge_tools_root``), and the sandbox is given
  that root instead. Copies, not symlinks: the jail bind-mounts one root at
  ``/.tools``, and a link out of it resolves to nothing inside the chroot.
- ``kind: http`` — the API pod ships no tools; sandbox-host owns ``/.tools``.
  A ``{bundle: …}`` plugin means "already in sandbox-host ``builtin/``", and an
  ``{artifact: url}`` plugin is resolved like an app's #674 ``external_tools``.
- ``kind: docker`` — no tools support, so no plugin sandbox halves either.

Plugin commands are never agent tools: they reach the sandbox, not any app's
tool ceiling. That is why nothing here produces a ``PackageInfo`` for the
agent-facing package list.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from workspace_app.view_plugins.discovery import ViewPlugin, ViewPluginError

logger = logging.getLogger(__name__)


def bundle_plugins(plugins: Sequence[ViewPlugin]) -> list[ViewPlugin]:
    """The plugins whose sandbox half ships as a bundle inside the plugin."""
    return [p for p in plugins if p.manifest.sandbox is not None and p.manifest.sandbox.bundle]


def artifact_plugins(plugins: Sequence[ViewPlugin]) -> dict[str, str]:
    """``{plugin name: #674 artifact URL}`` for the plugins resolved that way."""
    return {
        p.name: p.manifest.sandbox.artifact
        for p in plugins
        if p.manifest.sandbox is not None and p.manifest.sandbox.artifact
    }


def _link_or_copy(src: str, dst: str) -> None:
    """Hard-link a prebuilt package's file (a python + venv is hundreds of MB,
    and this runs every boot); copy when the two dirs are on different
    filesystems."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _stamp(pairs) -> str:
    """What the merged root was built from, per mounted name: a prebuilt
    bundle's `.built` marker (its bytes, inode and mtime), else every source
    file's path, size and mtime."""
    h = hashlib.sha256()
    for target, src in pairs:
        h.update(f"{target}\0".encode())
        built = src / ".built"
        if built.is_file():
            # A prebuilt bundle's own build stamp (source hash + launcher
            # fingerprint) changes on every rebuild — one read instead of a
            # stat per file of a python + venv (seconds, measured, at boot).
            # Bytes AND identity: a forced rebuild of the same source writes
            # the same bytes into a NEW file (`build_package` rmtrees first),
            # and the hard links in the merged root still point at old inodes.
            st = built.stat()
            h.update(built.read_bytes() + f"\0{st.st_ino}\0{st.st_mtime_ns}".encode())
            continue
        for f in sorted(src.rglob("*")):
            st = f.lstat()
            h.update(f"{f.relative_to(src)}\0{st.st_size}\0{st.st_mtime_ns}\0".encode())
    return h.hexdigest()


def merge_tools_root(
    prebuilt: Path | None,
    packages: Sequence[str],
    plugins: Sequence[ViewPlugin],
    dst: Path,
) -> Path | None:
    """The tools root the local sandbox should mount.

    With no bundle plugins this is ``prebuilt`` unchanged (or ``None`` when
    there are no packages either) — a deployment without plugins sees exactly
    what it saw before. Otherwise ``dst`` is (re)built when its sources changed: every package
    in ``packages`` hard-linked from ``prebuilt``, every plugin bundle copied.
    Built beside ``dst`` and swapped in, so a crash mid-copy never leaves a
    half-merged root behind for the next boot to trust; under an exclusive lock,
    and skipped entirely when a stamp of the sources says ``dst`` is current —
    so a second process booting on the same filesystem never tears down the
    tree the first one's live sandboxes are using.

    A bundle plugin whose bundle folder is ABSENT (the API image ships web
    halves only) is skipped with a boot line naming it, not refused.

    Raises ``ViewPluginError`` naming both sides when a plugin and a tool
    package share a name (both would be ``/.tools/<name>``) — absent bundle or
    not — and naming the plugin when a PRESENT bundle has no executable
    ``launch``."""
    bundles = bundle_plugins(plugins)
    if not bundles:
        return prebuilt if packages else None
    taken = set(packages)
    for p in bundles:
        assert p.manifest.sandbox is not None and p.manifest.sandbox.bundle is not None
        if p.name in taken:
            raise ViewPluginError(
                f"view plugin {p.name!r} and tool package {p.name!r} would both be "
                f"/.tools/{p.name} — rename the plugin (its folder and plugin.json `name`)"
            )
    present = []
    for p in bundles:
        assert p.manifest.sandbox is not None and p.manifest.sandbox.bundle is not None
        if not (p.dir / p.manifest.sandbox.bundle).is_dir():
            # The API image's plugin stage builds WEB halves only (under
            # `kind: http` the sandbox half lives in sandbox-host). Refusing boot
            # here took every pod of a default `kind: local` deploy down; instead
            # the plugin's commands fail per call, naming it (the runner's 502,
            # show_file's "could not check" note). `print`: a boot line an
            # operator reads in `kubectl logs`.
            print(
                f"  ⚠ view plugin {p.name}: sandbox.bundle {p.manifest.sandbox.bundle!r} is not "
                "in this plugin dir, so its sandbox commands are unavailable here — install it "
                "with `python -m workspace_app.view_plugin build`"
            )
            continue
        present.append(p)
        launch = p.dir / p.manifest.sandbox.bundle / "launch"
        if not launch.is_file() or not os.access(launch, os.X_OK):
            raise ViewPluginError(
                f"view plugin {p.name!r} ({p.dir}): sandbox.bundle "
                f"{p.manifest.sandbox.bundle!r} has no executable `launch` — it must be a "
                "prebuilt tool bundle"
            )
    bundles = present
    if not bundles:
        return prebuilt if packages else None
    sources = [prebuilt / name for name in packages if prebuilt is not None] + [
        p.dir / p.manifest.sandbox.bundle
        for p in bundles
        if p.manifest.sandbox is not None and p.manifest.sandbox.bundle is not None
    ]
    targets = [*packages, *(p.name for p in bundles)]
    stamp = _stamp(zip(targets, sources, strict=True))
    stamp_file = dst.with_name(f"{dst.name}.stamp")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # One merge at a time per root: the API and the blob-gc worker both boot
    # `build_app`, on the same filesystem under `kind: local`.
    with open(dst.with_name(f"{dst.name}.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if dst.is_dir() and stamp_file.is_file() and stamp_file.read_text() == stamp:
            # Unchanged — and possibly under live sandboxes of another process
            # that merged it first. Leave it exactly as it is.
            return dst
        # Under the lock no other merge is running, so any staging/retired tree
        # here is a crashed merge's leftover, whatever pid it was named for.
        for d in dst.parent.glob(f"{dst.name}.staging-*"):
            shutil.rmtree(d)
        for d in dst.parent.glob(f"{dst.name}.retired-*"):
            shutil.rmtree(d)
        # Forget the old stamp first: a crash between the swap and the new
        # stamp must not leave a stamp describing a tree that is gone.
        stamp_file.unlink(missing_ok=True)
        staging = dst.with_name(f"{dst.name}.staging-{os.getpid()}")
        retired = dst.with_name(f"{dst.name}.retired-{os.getpid()}")
        staging.mkdir()
        for name in packages:
            assert prebuilt is not None
            shutil.copytree(
                prebuilt / name, staging / name, symlinks=True, copy_function=_link_or_copy
            )
        for p in bundles:
            assert p.manifest.sandbox is not None and p.manifest.sandbox.bundle is not None
            shutil.copytree(p.dir / p.manifest.sandbox.bundle, staging / p.name, symlinks=True)
        if dst.exists():
            dst.rename(retired)
        staging.rename(dst)
        stamp_file.write_text(stamp)
        if retired.exists():
            shutil.rmtree(retired)
    logger.info(
        "view plugins: tools root %s = %d package(s) + plugin bundle(s) %s",
        dst,
        len(packages),
        [p.name for p in bundles],
    )
    return dst
