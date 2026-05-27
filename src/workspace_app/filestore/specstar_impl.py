from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import quote

from msgspec import Struct, field
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from .protocol import FileExists, FileNotFound, dir_ancestors


class _WorkspaceFiles(Struct):
    workspace_id: str
    files: dict[str, bytes]
    dirs: list[str] = field(default_factory=list)


def _rid(workspace_id: str) -> str:
    """Deterministic resource id for a workspace's files: the workspace_id,
    percent-encoded slash-free. EVERY pod computes the same id, so any pod
    reads/writes the one shared record — no per-instance cache.

    This is the multi-pod fix (#16): the old code cached workspace_id→resource_id
    in memory, so a second pod (empty cache) created a DUPLICATE _WorkspaceFiles
    and the two pods couldn't see each other's files.
    """
    return quote(workspace_id, safe="")


class SpecstarFileStore:
    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec
        # Idempotent registration so two instances can share one store (the
        # multi-pod scenario + its test); a real pod registers once.
        with contextlib.suppress(ValueError):
            spec.add_model(_WorkspaceFiles)
        self._rm = spec.get_resource_manager(_WorkspaceFiles)

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

    def _load(self, workspace_id: str) -> _WorkspaceFiles | None:
        """This workspace's record from the shared store, or None if untouched.
        No cache — keyed by the deterministic id, so it's correct from any pod."""
        try:
            return self._rm.get(_rid(workspace_id)).data
        except ResourceIDNotFoundError:
            return None

    def _write_sync(self, workspace_id: str, path: str, data: bytes) -> None:
        current = self._load(workspace_id)
        if current is None:
            self._rm.create(
                _WorkspaceFiles(
                    workspace_id=workspace_id,
                    files={path: data},
                    dirs=dir_ancestors(path),
                ),
                resource_id=_rid(workspace_id),
            )
            return
        current.files[path] = data
        current.dirs = sorted(set(current.dirs) | set(dir_ancestors(path)))
        self._rm.update(_rid(workspace_id), current)

    def _mkdir_sync(self, workspace_id: str, path: str) -> None:
        if path in self._files_or_empty(workspace_id):
            raise FileExists(f"{workspace_id}:{path}")
        current = self._load(workspace_id)
        new_dirs = {path, *dir_ancestors(path)}
        if current is None:
            self._rm.create(
                _WorkspaceFiles(workspace_id=workspace_id, files={}, dirs=sorted(new_dirs)),
                resource_id=_rid(workspace_id),
            )
            return
        current.dirs = sorted(set(current.dirs) | new_dirs)
        self._rm.update(_rid(workspace_id), current)

    def _rmdir_sync(self, workspace_id: str, path: str) -> None:
        current = self._load(workspace_id)
        if current is None or path not in current.dirs:
            raise FileNotFound(f"{workspace_id}:{path}")
        under = path + "/"
        current.dirs = [d for d in current.dirs if d != path and not d.startswith(under)]
        for p in [p for p in current.files if p.startswith(under)]:
            del current.files[p]
        self._rm.update(_rid(workspace_id), current)

    def _dirs_or_empty(self, workspace_id: str) -> list[str]:
        current = self._load(workspace_id)
        return current.dirs if current else []

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
        current = self._load(workspace_id)
        if current is None or path not in current.files:
            raise FileNotFound(f"{workspace_id}:{path}")
        del current.files[path]
        self._rm.update(_rid(workspace_id), current)

    def _files_or_empty(self, workspace_id: str) -> dict[str, bytes]:
        current = self._load(workspace_id)
        return current.files if current else {}
