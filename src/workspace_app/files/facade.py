"""WorkspaceFiles — see package docstring. P1 is a thin pass-through to
FileStore; the method surface is exactly what the agent tools + API file routes
need, so P2 can change the routing behind it without touching callers."""

from __future__ import annotations

from ..filestore.protocol import FileStore


class WorkspaceFiles:
    def __init__(self, filestore: FileStore) -> None:
        self._fs = filestore

    async def read(self, workspace_id: str, path: str) -> bytes:
        return await self._fs.read(workspace_id, path)

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        await self._fs.write(workspace_id, path, data)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await self._fs.ls(workspace_id, prefix)

    async def exists(self, workspace_id: str, path: str) -> bool:
        return await self._fs.exists(workspace_id, path)

    async def delete(self, workspace_id: str, path: str) -> None:
        await self._fs.delete(workspace_id, path)

    async def mkdir(self, workspace_id: str, path: str) -> None:
        await self._fs.mkdir(workspace_id, path)

    async def rmdir(self, workspace_id: str, path: str) -> None:
        await self._fs.rmdir(workspace_id, path)

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        return await self._fs.is_dir(workspace_id, path)

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await self._fs.listdir(workspace_id, prefix)
