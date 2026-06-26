"""SandboxSync — moves files between the live Sandbox (the single source of
truth while warm) and the FileStore snapshot (durable backup + restore source).

Two operations now that the sandbox is authoritative (sandbox-as-SoT redesign):

- restore: pull every snapshot path into a freshly-woken sandbox so the agent's
  shell starts with the files it left behind, and seed the diff state.
- mirror:  PULL the live sandbox into the snapshot — copy files whose opaque
  `version` changed since the last mirror, and delete snapshot files the
  sandbox no longer has. A complete, deletion-aware mirror (so a restarted
  sandbox restores the exact last state). Driven on a ≤window throttle while a
  turn is active + forced at turn-end / idle-kill / close.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from ..filestore.protocol import FileStore
from ..sandbox.protocol import Sandbox, SandboxHandle
from .ignore import DEFAULT_IGNORES, should_ignore


@contextlib.contextmanager
def _staging_file() -> Iterator[Path]:
    """A short-lived on-disk hand-off file for streaming one file between the
    sandbox and the FileStore — so neither side holds the whole file in RAM
    (issue #219). Removed on exit."""
    fd, name = tempfile.mkstemp(prefix="wsfile-")
    os.close(fd)
    tmp = Path(name)
    try:
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)


class SandboxSync:
    def __init__(
        self,
        filestore: FileStore,
        sandbox: Sandbox,
        *,
        ignores: list[str] | None = None,
    ) -> None:
        self._fs = filestore
        self._sb = sandbox
        self._ignores = list(ignores) if ignores is not None else list(DEFAULT_IGNORES)
        # Per-workspace {path: version} last mirrored to the snapshot — the diff
        # state so `mirror` only re-copies changed files and can spot deletions.
        self._versions: dict[str, dict[str, str]] = {}

    async def restore(self, workspace_id: str, handle: SandboxHandle) -> int:
        n = 0
        for path in await self._fs.ls(workspace_id):
            # Stream FileStore → sandbox through a staging file so a big file
            # never sits whole in RAM on wake (issue #219).
            with _staging_file() as tmp:
                await self._fs.read_to_file(workspace_id, path, tmp)
                await self._sb.upload_file(handle, tmp, path)
            n += 1
        # Seed the diff state from the just-restored sandbox so the first mirror
        # after a wake is a no-op (nothing has changed yet).
        self._versions[workspace_id] = {
            e.path: e.version
            for e in await self._sb.walk(handle, "/")
            if not should_ignore(e.path, self._ignores, e.size)
        }
        return n

    async def mirror(self, workspace_id: str, handle: SandboxHandle) -> int:
        """PULL the live sandbox into the snapshot: copy files whose `version`
        changed since the last mirror, and delete snapshot files the sandbox no
        longer has (a complete, deletion-aware mirror). Returns how many paths
        were written or deleted."""
        prev = self._versions.get(workspace_id, {})
        seen: dict[str, str] = {}
        n = 0
        for entry in await self._sb.walk(handle, "/"):
            if should_ignore(entry.path, self._ignores, entry.size):
                continue
            seen[entry.path] = entry.version
            if prev.get(entry.path) == entry.version:
                continue  # unchanged since last mirror
            # Stream sandbox → FileStore through a staging file so a big file
            # the agent produced never sits whole in RAM (issue #219).
            with _staging_file() as tmp:
                await self._sb.download_to_file(handle, entry.path, tmp)
                await self._fs.write_from_path(workspace_id, entry.path, tmp, None)
            n += 1
        for path in prev:
            if path not in seen and await self._fs.exists(workspace_id, path):
                await self._fs.delete(workspace_id, path)
                n += 1
        self._versions[workspace_id] = seen
        return n
