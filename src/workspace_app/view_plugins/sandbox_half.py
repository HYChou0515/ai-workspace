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


def merge_tools_root(
    prebuilt: Path | None,
    packages: Sequence[str],
    plugins: Sequence[ViewPlugin],
    dst: Path,
) -> Path | None:
    """The tools root the local sandbox should mount.

    With no bundle plugins this is ``prebuilt`` unchanged (or ``None`` when
    there are no packages either) — a deployment without plugins sees exactly
    what it saw before. Otherwise ``dst`` is rebuilt from scratch: every package
    in ``packages`` hard-linked from ``prebuilt``, every plugin bundle copied.
    Built beside ``dst`` and swapped in, so a crash mid-copy never leaves a
    half-merged root behind for the next boot to trust.

    Raises ``ViewPluginError`` naming both sides when a plugin and a tool
    package share a name (both would be ``/.tools/<name>``), and naming the
    plugin when its bundle has no ``launch``."""
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
        launch = p.dir / p.manifest.sandbox.bundle / "launch"
        if not launch.is_file() or not os.access(launch, os.X_OK):
            raise ViewPluginError(
                f"view plugin {p.name!r} ({p.dir}): sandbox.bundle "
                f"{p.manifest.sandbox.bundle!r} has no executable `launch` — it must be a "
                "prebuilt tool bundle"
            )
    staging = dst.with_name(f"{dst.name}.staging")
    retired = dst.with_name(f"{dst.name}.retired")
    for d in (staging, retired):
        if d.exists():
            shutil.rmtree(d)
    staging.mkdir(parents=True)
    for name in packages:
        assert prebuilt is not None
        shutil.copytree(prebuilt / name, staging / name, symlinks=True, copy_function=_link_or_copy)
    for p in bundles:
        assert p.manifest.sandbox is not None and p.manifest.sandbox.bundle is not None
        shutil.copytree(p.dir / p.manifest.sandbox.bundle, staging / p.name, symlinks=True)
    if dst.exists():
        dst.rename(retired)
    staging.rename(dst)
    if retired.exists():
        shutil.rmtree(retired)
    logger.info(
        "view plugins: tools root %s = %d package(s) + plugin bundle(s) %s",
        dst,
        len(packages),
        [p.name for p in bundles],
    )
    return dst
