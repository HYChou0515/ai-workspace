"""In-process FileStore — no specstar dependency.

Used as the default in `__main__.py` so the running app doesn't leak
the ~19 internal `/-workspacefiles/*` specstar CRUD routes into the
OpenAPI surface (those routes existed because the previous default
backend, SpecstarFileStore, registers a storage model with specstar
which auto-emits CRUD endpoints for it).

Persistence: none. Restart wipes state. Fine for single-host dev /
demo; swap in SpecstarFileStore + a disk-backed specstar storage
when persistence matters.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from .protocol import FileExists, FileNotFound, dir_ancestors


class MemoryFileStore:
    def __init__(self) -> None:
        self._files: dict[str, dict[str, bytes]] = defaultdict(dict)
        self._dirs: dict[str, set[str]] = defaultdict(set)
        self._dirty: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        async with self._lock:
            self._files[workspace_id][path] = data
            self._dirs[workspace_id].update(dir_ancestors(path))
            self._dirty[workspace_id].add(path)

    async def read(self, workspace_id: str, path: str) -> bytes:
        async with self._lock:
            files = self._files.get(workspace_id, {})
            if path not in files:
                raise FileNotFound(f"{workspace_id}:{path}")
            return files[path]

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        async with self._lock:
            return [p for p in self._files.get(workspace_id, {}) if p.startswith(prefix)]

    async def exists(self, workspace_id: str, path: str) -> bool:
        async with self._lock:
            return path in self._files.get(workspace_id, {})

    async def delete(self, workspace_id: str, path: str) -> None:
        async with self._lock:
            files = self._files.get(workspace_id)
            if files is None or path not in files:
                raise FileNotFound(f"{workspace_id}:{path}")
            del files[path]  # parent dirs intentionally persist (honest FS)

    async def mkdir(self, workspace_id: str, path: str) -> None:
        async with self._lock:
            if path in self._files.get(workspace_id, {}):
                raise FileExists(f"{workspace_id}:{path}")
            self._dirs[workspace_id].add(path)
            self._dirs[workspace_id].update(dir_ancestors(path))

    async def rmdir(self, workspace_id: str, path: str) -> None:
        async with self._lock:
            dirs = self._dirs.get(workspace_id, set())
            if path not in dirs:
                raise FileNotFound(f"{workspace_id}:{path}")
            under = path + "/"
            dirs.difference_update({d for d in dirs if d == path or d.startswith(under)})
            files = self._files.get(workspace_id, {})
            for p in [p for p in files if p.startswith(under)]:
                del files[p]

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        async with self._lock:
            return path in self._dirs.get(workspace_id, set())

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        async with self._lock:
            return [d for d in self._dirs.get(workspace_id, set()) if d.startswith(prefix)]

    def dirty_paths(self, workspace_id: str) -> set[str]:
        return set(self._dirty.get(workspace_id, set()))

    def clear_dirty(self, workspace_id: str) -> None:
        self._dirty.pop(workspace_id, None)
