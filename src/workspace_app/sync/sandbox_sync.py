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

from ..filestore.protocol import FileStore
from ..sandbox.protocol import Sandbox, SandboxHandle
from .ignore import DEFAULT_IGNORES, should_ignore


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
            data = await self._fs.read(workspace_id, path)
            await self._sb.upload(handle, data, path)
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
            data = await self._sb.download(handle, entry.path)
            await self._fs.write(workspace_id, entry.path, data)
            n += 1
        for path in prev:
            if path not in seen and await self._fs.exists(workspace_id, path):
                await self._fs.delete(workspace_id, path)
                n += 1
        self._versions[workspace_id] = seen
        return n
