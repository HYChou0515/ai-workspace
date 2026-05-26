from __future__ import annotations

import asyncio

from msgspec import Struct, field
from specstar import SpecStar

from .protocol import FileExists, FileNotFound, dir_ancestors


class _WorkspaceFiles(Struct):
    workspace_id: str
    files: dict[str, bytes]
    dirs: list[str] = field(default_factory=list)


class SpecstarFileStore:
    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec
        spec.add_model(_WorkspaceFiles)
        self._rm = spec.get_resource_manager(_WorkspaceFiles)
        self._ids: dict[str, str] = {}

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, workspace_id, path, data)

    async def read(self, workspace_id: str, path: str) -> bytes:
        return await asyncio.to_thread(self._read_sync, workspace_id, path)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._ls_sync, workspace_id, prefix)

    async def exists(self, workspace_id: str, path: str) -> bool:
        return await asyncio.to_thread(self._exists_sync, workspace_id, path)

    async def delete(self, workspace_id: str, path: str) -> None:
        await asyncio.to_thread(self._delete_sync, workspace_id, path)

    async def mkdir(self, workspace_id: str, path: str) -> None:
        await asyncio.to_thread(self._mkdir_sync, workspace_id, path)

    async def rmdir(self, workspace_id: str, path: str) -> None:
        await asyncio.to_thread(self._rmdir_sync, workspace_id, path)

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        return await asyncio.to_thread(lambda: path in self._dirs_or_empty(workspace_id))

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(
            lambda: [d for d in self._dirs_or_empty(workspace_id) if d.startswith(prefix)]
        )

    def _write_sync(self, workspace_id: str, path: str, data: bytes) -> None:
        rid = self._ids.get(workspace_id)
        if rid is None:
            rev = self._rm.create(
                _WorkspaceFiles(
                    workspace_id=workspace_id,
                    files={path: data},
                    dirs=dir_ancestors(path),
                )
            )
            self._ids[workspace_id] = rev.resource_id
            return
        current = self._rm.get(rid).data
        current.files[path] = data
        current.dirs = sorted(set(current.dirs) | set(dir_ancestors(path)))
        self._rm.update(rid, current)

    def _mkdir_sync(self, workspace_id: str, path: str) -> None:
        if path in self._files_or_empty(workspace_id):
            raise FileExists(f"{workspace_id}:{path}")
        rid = self._ids.get(workspace_id)
        new_dirs = {path, *dir_ancestors(path)}
        if rid is None:
            rev = self._rm.create(
                _WorkspaceFiles(workspace_id=workspace_id, files={}, dirs=sorted(new_dirs))
            )
            self._ids[workspace_id] = rev.resource_id
            return
        current = self._rm.get(rid).data
        current.dirs = sorted(set(current.dirs) | new_dirs)
        self._rm.update(rid, current)

    def _rmdir_sync(self, workspace_id: str, path: str) -> None:
        rid = self._ids.get(workspace_id)
        if rid is None:
            raise FileNotFound(f"{workspace_id}:{path}")
        current = self._rm.get(rid).data
        if path not in current.dirs:
            raise FileNotFound(f"{workspace_id}:{path}")
        under = path + "/"
        current.dirs = [d for d in current.dirs if d != path and not d.startswith(under)]
        for p in [p for p in current.files if p.startswith(under)]:
            del current.files[p]
        self._rm.update(rid, current)

    def _dirs_or_empty(self, workspace_id: str) -> list[str]:
        rid = self._ids.get(workspace_id)
        if rid is None:
            return []
        return self._rm.get(rid).data.dirs

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
