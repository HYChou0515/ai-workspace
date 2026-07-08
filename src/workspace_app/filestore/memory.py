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
import logging
from collections import defaultdict
from pathlib import Path

from .protocol import FileExists, FileNotFound, dir_ancestors

logger = logging.getLogger(__name__)


class MemoryFileStore:
    def __init__(self) -> None:
        self._files: dict[str, dict[str, bytes]] = defaultdict(dict)
        self._dirs: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        async with self._lock:
            self._files[workspace_id][path] = data
            self._dirs[workspace_id].update(dir_ancestors(path))

    async def create_exclusive(self, workspace_id: str, path: str, data: bytes) -> None:
        """Atomic create-if-absent (#419 N1 numbering arbiter): write `data` iff
        `path` doesn't exist, else raise `FileExists`. Atomic because the whole
        check+set runs under `self._lock` with no `await` inside — two racing
        claimants can't both win one path."""
        async with self._lock:
            if path in self._files.get(workspace_id, {}):
                logger.debug("memory: create_exclusive %s:%s rejected, exists", workspace_id, path)
                raise FileExists(path)
            self._files[workspace_id][path] = data
            self._dirs[workspace_id].update(dir_ancestors(path))

    async def write_from_path(
        self, workspace_id: str, path: str, source: Path, content_type: str | None
    ) -> None:
        # In-memory store keeps bytes in RAM regardless (test-only backend), so
        # there's nothing to stream — just read the staged file and store it.
        await self.write(workspace_id, path, await asyncio.to_thread(source.read_bytes))

    async def read(self, workspace_id: str, path: str) -> bytes:
        async with self._lock:
            files = self._files.get(workspace_id, {})
            if path not in files:
                raise FileNotFound(f"{workspace_id}:{path}")
            return files[path]

    async def read_to_file(self, workspace_id: str, path: str, dest: Path) -> None:
        data = await self.read(workspace_id, path)
        await asyncio.to_thread(dest.write_bytes, data)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        async with self._lock:
            return [p for p in self._files.get(workspace_id, {}) if p.startswith(prefix)]

    async def stat_all(self, workspace_id: str, prefix: str = "") -> list[tuple[str, int]]:
        """#362: every file under ``prefix`` as ``(path, size)`` — the sizes are
        already in hand (we hold the bytes), so no per-file read is needed."""
        async with self._lock:
            return [
                (p, len(b))
                for p, b in self._files.get(workspace_id, {}).items()
                if p.startswith(prefix)
            ]

    async def exists(self, workspace_id: str, path: str) -> bool:
        async with self._lock:
            return path in self._files.get(workspace_id, {})

    async def workspace_usage(self, workspace_id: str) -> int:
        async with self._lock:
            return sum(len(b) for b in self._files.get(workspace_id, {}).values())

    async def file_size(self, workspace_id: str, path: str) -> int | None:
        async with self._lock:
            files = self._files.get(workspace_id, {})
            return len(files[path]) if path in files else None

    async def census(self) -> dict[str, int]:
        """#407: total files, distinct (non-empty) workspaces, and the largest
        workspace's file count — the same shape SpecstarFileStore reports."""
        async with self._lock:
            counts = [len(files) for files in self._files.values() if files]
        return {
            "total_workspacefile_rows": sum(counts),
            "n_workspaces": len(counts),
            "max_files_per_ws": max(counts, default=0),
        }

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
            logger.debug("memory: rmdir %s:%s removing subtree", workspace_id, path)
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
