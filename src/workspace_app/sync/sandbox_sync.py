"""SandboxSync — moves files between FileStore (durable) and Sandbox
(ephemeral execution context).

Three operations match the lifecycle hooks designed in plan-backend
§3.4:

- restore: pull every FileStore path into a freshly-created sandbox so
  the agent's shell starts with all the files it left behind last time.
- flush:   upload paths the agent has written via the FileStore-backed
  tools since the last sync, just before the agent runs a shell command
  that might want to read them.
- reverse: download sandbox FS changes back into FileStore on idle-kill
  or explicit teardown, so they survive past the sandbox's lifetime.

Deletions inside the sandbox are *not* propagated for v1 (Q11) —
removing a file from FileStore on every successful reverse-sync is too
dangerous when the agent might legitimately not have touched a file
this turn. Manual cleanup via the upcoming Files API is the answer.
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

    async def restore(self, workspace_id: str, handle: SandboxHandle) -> int:
        n = 0
        for path in await self._fs.ls(workspace_id):
            data = await self._fs.read(workspace_id, path)
            await self._sb.upload(handle, data, path)
            n += 1
        # The restore wrote into the sandbox using paths FileStore already
        # has — no NEW dirty paths, so don't conflate them with future
        # agent writes.
        self._fs.clear_dirty(workspace_id)
        return n

    async def flush(self, workspace_id: str, handle: SandboxHandle) -> int:
        dirty = self._fs.dirty_paths(workspace_id)
        for path in dirty:
            data = await self._fs.read(workspace_id, path)
            await self._sb.upload(handle, data, path)
        self._fs.clear_dirty(workspace_id)
        return len(dirty)

    async def reverse(self, workspace_id: str, handle: SandboxHandle) -> int:
        n = 0
        for entry in await self._sb.walk(handle, "/"):
            if should_ignore(entry.path, self._ignores, entry.size):
                continue
            data = await self._sb.download(handle, entry.path)
            if await self._fs.exists(workspace_id, entry.path):
                existing = await self._fs.read(workspace_id, entry.path)
                if existing == data:
                    continue
            await self._fs.write(workspace_id, entry.path, data)
            n += 1
        # We just wrote back through FileStore.write which marked these
        # paths dirty — but the sandbox already has them. Clearing keeps
        # the next flush a no-op (avoids reuploading what we just pulled).
        self._fs.clear_dirty(workspace_id)
        return n
