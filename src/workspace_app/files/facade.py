"""WorkspaceFiles — the single chokepoint for workspace file access.

It routes by **sandbox liveness**: when a sandbox is up for the workspace (the
single source of truth), reads/writes go there; when it's cold, they fall back
to the FileStore snapshot. A cold sandbox is frozen, so the snapshot is its
exact image — there's never a moment where two writable sources diverge.

`is_dir`/`listdir` are derived from `walk` when warm (the Sandbox Protocol has
no native dir listing); cold, they read the FileStore which tracks dirs
first-class. Constructed without a sandbox (`sandbox=None`), it degrades to a
plain FileStore pass-through — handy for tests + the transitional fallback.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable

from ..filestore.protocol import FileNotFound, FileStore
from ..sandbox.protocol import Sandbox, SandboxHandle


class WorkspaceFiles:
    def __init__(
        self,
        filestore: FileStore,
        sandbox: Sandbox | None = None,
        handle_for: Callable[[str], SandboxHandle | None] | None = None,
    ) -> None:
        self._fs = filestore
        self._sb = sandbox
        self._handle_for = handle_for
        # Per-(workspace, path) lock so a compare-and-swap (read → check →
        # write) is atomic against other writers going through this facade.
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def _warm(self, workspace_id: str) -> tuple[Sandbox, SandboxHandle] | None:
        """The live sandbox for this workspace, or None when it's cold."""
        if self._sb is None or self._handle_for is None:
            return None
        handle = self._handle_for(workspace_id)
        return (self._sb, handle) if handle is not None else None

    async def read(self, workspace_id: str, path: str) -> bytes:
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                return await sb.download(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        return await self._fs.read(workspace_id, path)

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            await sb.upload(h, data, path)
        else:
            await self._fs.write(workspace_id, path, data)

    async def exists(self, workspace_id: str, path: str) -> bool:
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return await sb.exists(h, path)
        return await self._fs.exists(workspace_id, path)

    async def delete(self, workspace_id: str, path: str) -> None:
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                await sb.delete(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        else:
            await self._fs.delete(workspace_id, path)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return [e.path for e in await sb.walk(h, prefix or "/")]
        return await self._fs.ls(workspace_id, prefix)

    async def mkdir(self, workspace_id: str, path: str) -> None:
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            await sb.mkdir(h, path)
        else:
            await self._fs.mkdir(workspace_id, path)

    async def rmdir(self, workspace_id: str, path: str) -> None:
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                await sb.rmdir(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        else:
            await self._fs.rmdir(workspace_id, path)

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            base = path.rstrip("/") + "/"
            return any(e.path.startswith(base) for e in await sb.walk(h, "/"))
        return await self._fs.is_dir(workspace_id, path)

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            dirs: set[str] = set()
            for e in await sb.walk(h, prefix or "/"):
                parts = e.path.strip("/").split("/")
                for i in range(1, len(parts)):
                    dirs.add("/" + "/".join(parts[:i]))
            return sorted(dirs)
        return await self._fs.listdir(workspace_id, prefix)

    # ---- compare-and-swap writes (the agent must declare its expectation) ----

    async def create(self, workspace_id: str, path: str, data: bytes) -> bytes | None:
        """Create-only write: succeed (return None) if `path` doesn't exist;
        otherwise don't clobber — return the current bytes so the caller can
        decide. Atomic under the per-path lock."""
        async with self._locks[(workspace_id, path)]:
            if await self.exists(workspace_id, path):
                return await self.read(workspace_id, path)
            await self.write(workspace_id, path, data)
            return None

    async def edit(self, workspace_id: str, path: str, old: str, new: str) -> str | None:
        """Replace the **unique** occurrence of `old` with `new`. Succeed
        (return None) only when `old` appears exactly once; otherwise it's a
        conflict (missing file, text not found, or ambiguous) and the current
        text is returned so the caller can re-base. Atomic under the per-path
        lock — so a concurrent change makes `old` stop matching and the edit is
        rejected rather than blindly applied."""
        async with self._locks[(workspace_id, path)]:
            try:
                current = (await self.read(workspace_id, path)).decode("utf-8", errors="replace")
            except FileNotFound:
                return ""
            if current.count(old) != 1:
                return current
            await self.write(workspace_id, path, current.replace(old, new, 1).encode("utf-8"))
            return None
