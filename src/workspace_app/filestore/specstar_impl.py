from __future__ import annotations

import asyncio

from msgspec import Struct
from specstar import SpecStar

from .protocol import FileNotFound


class _WorkspaceFiles(Struct):
    workspace_id: str
    files: dict[str, bytes]


class SpecstarFileStore:
    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec
        spec.add_model(_WorkspaceFiles)
        self._rm = spec.get_resource_manager(_WorkspaceFiles)
        self._ids: dict[str, str] = {}
        self._dirty: dict[str, set[str]] = {}

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, workspace_id, path, data)
        self._dirty.setdefault(workspace_id, set()).add(path)

    def dirty_paths(self, workspace_id: str) -> set[str]:
        return set(self._dirty.get(workspace_id, set()))

    def clear_dirty(self, workspace_id: str) -> None:
        self._dirty.pop(workspace_id, None)

    async def read(self, workspace_id: str, path: str) -> bytes:
        return await asyncio.to_thread(self._read_sync, workspace_id, path)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._ls_sync, workspace_id, prefix)

    async def exists(self, workspace_id: str, path: str) -> bool:
        return await asyncio.to_thread(self._exists_sync, workspace_id, path)

    async def delete(self, workspace_id: str, path: str) -> None:
        await asyncio.to_thread(self._delete_sync, workspace_id, path)

    def _write_sync(self, workspace_id: str, path: str, data: bytes) -> None:
        rid = self._ids.get(workspace_id)
        if rid is None:
            rev = self._rm.create(_WorkspaceFiles(workspace_id=workspace_id, files={path: data}))
            self._ids[workspace_id] = rev.resource_id
            return
        current = self._rm.get(rid).data
        current.files[path] = data
        self._rm.update(rid, current)

    def _read_sync(self, workspace_id: str, path: str) -> bytes:
        files = self._files_or_empty(workspace_id)
        if path not in files:
            raise FileNotFound(f"{workspace_id}:{path}")
        return files[path]

    def _ls_sync(self, workspace_id: str, prefix: str) -> list[str]:
        return [p for p in self._files_or_empty(workspace_id) if p.startswith(prefix)]

    def _exists_sync(self, workspace_id: str, path: str) -> bool:
        return path in self._files_or_empty(workspace_id)

    def _delete_sync(self, workspace_id: str, path: str) -> None:
        rid = self._ids.get(workspace_id)
        if rid is None:
            raise FileNotFound(f"{workspace_id}:{path}")
        current = self._rm.get(rid).data
        if path not in current.files:
            raise FileNotFound(f"{workspace_id}:{path}")
        del current.files[path]
        self._rm.update(rid, current)

    def _files_or_empty(self, workspace_id: str) -> dict[str, bytes]:
        rid = self._ids.get(workspace_id)
        if rid is None:
            return {}
        return self._rm.get(rid).data.files
