"""Sandbox tool provisioning — copy each PackageInfo's prebuilt bundle
into the sandbox.

The bundle was already built by ``workspace_app.tooling.prebuild`` into
``PREBUILT_DIR/<pkg>/`` (relocatable venv + portable python + launch +
schemas). At provision time we tar that tree up and drop it at the
sandbox-relative ``install_dir`` (typically ``../.tools/<pkg>``); the
sandbox needs no uv / network / build step. ``invoke`` then runs
``<install_dir>/launch <cmd> '<args-json>'`` — the 3-stage contract.

See ``docs/plan-skills-and-tools.md`` §B.4–§B.6.
"""

from __future__ import annotations

import io
import logging
import tarfile
from collections.abc import Sequence
from pathlib import Path

from ..sandbox.protocol import ExecResult, Sandbox, SandboxHandle
from ..tooling.registry import PackageInfo

logger = logging.getLogger(__name__)


class ProvisionError(RuntimeError):
    """A package's archive failed to extract — the sandbox isn't usable
    for that package. Raised from ``provision_tools``; the API layer
    surfaces it as a 5xx with the underlying message."""

    def __init__(self, package: str, cmd: list[str], result: ExecResult) -> None:
        self.package = package
        self.cmd = cmd
        self.result = result
        super().__init__(
            f"provisioning {package!r} failed at `{' '.join(cmd)}` "
            f"(exit {result.exit_code}):\n{result.stdout.decode('utf-8', errors='replace')}"
        )


def _tar_tree(src: Path) -> bytes:
    """A gz tar of ``src``'s contents (children at the archive root),
    preserving permissions + symlinks — so an extracted relocatable venv
    still runs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for child in sorted(src.iterdir()):
            tar.add(child, arcname=child.name)
    return buf.getvalue()


async def provision_tools(
    sandbox: Sandbox,
    handle: SandboxHandle,
    packages: Sequence[PackageInfo],
    *,
    prebuilt_dir: Path,
) -> None:
    """Install each package's prebuilt bundle into the sandbox: tar it
    on the host, upload, then extract at ``pkg.install_dir`` (sandbox-
    relative). Raises ``ProvisionError`` on the first non-zero exit so
    a broken package halts provisioning loudly.

    No per-package setup step (`uv sync` / pip install / …) — everything
    deployable is baked into the prebuilt bundle, and the sandbox-side
    launcher script handles the AT_SECURE dynamic-loader workaround
    inherent to running inside the userns chroot jail."""
    for pkg in packages:
        host = prebuilt_dir / pkg.name
        dest = pkg.install_dir  # e.g. "../.tools/datalab"
        logger.info("provision: installing %s at %s", pkg.name, dest)
        archive = f"{dest}.provision.tar.gz"
        await sandbox.upload(handle, _tar_tree(host), archive)
        # --no-same-owner: inside the userns jail we run as mapped-root,
        # so restoring the host uid/gid would fail. Keep file modes
        # (the launcher needs exec bits) but skip chown.
        script = (
            f"mkdir -p {dest} && tar xzf {archive} -C {dest} --no-same-owner && rm -f {archive}"
        )
        extract = await sandbox.exec(handle, ["sh", "-c", script])
        logger.debug("provision: %s extract exit=%d", pkg.name, extract.exit_code)
        if extract.exit_code != 0:
            logger.warning("provision: %s extract failed (exit %d)", pkg.name, extract.exit_code)
            raise ProvisionError(pkg.name, ["tar", "-C", dest], extract)
