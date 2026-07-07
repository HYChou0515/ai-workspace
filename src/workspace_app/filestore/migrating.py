"""MigratingFileStore — the #492 M2 dual-read migration wrapper.

Cuts the durable workspace store over from the specstar-blob store (``legacy``)
to the NFS tree (``primary``) with ZERO downtime and no phantom data loss:

- **writes** go to ``primary`` only (NFS is the going-forward authority);
- **reads** try ``primary``, then fall back to ``legacy`` and lazily backfill
  the byte into ``primary`` (so the next read — and the host's rsync — see it);
- **listings** (`ls` / `listdir` / `exists` / `is_dir` / `stat_all` /
  `workspace_usage`) UNION both stores, so a workspace whose files are still
  only in ``legacy`` never looks empty mid-migration (that phantom emptiness IS
  the data-loss symptom we're fixing);
- **deletes** hit BOTH stores, so a removed file can't be resurrected by the
  union on the next read.

``legacy`` is treated as READ-ONLY (frozen) except for delete/rmdir propagation.
Once ``backfill_workspace`` (the sweeper's unit of work) has copied everything,
an operator retires ``legacy`` and swaps this wrapper for the bare ``primary``.
"""

from __future__ import annotations

from pathlib import Path

from .protocol import FileExists, FileNotFound, FileStore


class MigratingFileStore:
    def __init__(self, primary: FileStore, legacy: FileStore) -> None:
        self._primary = primary
        self._legacy = legacy

    # ── writes → primary only ────────────────────────────────────────────────

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        await self._primary.write(workspace_id, path, data)

    async def write_from_path(
        self, workspace_id: str, path: str, source: Path, content_type: str | None
    ) -> None:
        await self._primary.write_from_path(workspace_id, path, source, content_type)

    async def create_exclusive(self, workspace_id: str, path: str, data: bytes) -> None:
        # A path taken in EITHER store must block an exclusive create — else the
        # #419 numbering arbiter would hand out an id that already exists in the
        # not-yet-migrated legacy store. legacy is frozen, so this check is stable.
        if await self._legacy.exists(workspace_id, path):
            raise FileExists(path)
        await self._primary.create_exclusive(workspace_id, path, data)  # ty: ignore[unresolved-attribute]

    # ── reads → primary, else legacy + lazy backfill ─────────────────────────

    async def read(self, workspace_id: str, path: str) -> bytes:
        try:
            return await self._primary.read(workspace_id, path)
        except FileNotFound:
            data = await self._legacy.read(workspace_id, path)  # raises if truly absent
            await self._primary.write(workspace_id, path, data)  # backfill
            return data

    async def read_to_file(self, workspace_id: str, path: str, dest: Path) -> None:
        try:
            await self._primary.read_to_file(workspace_id, path, dest)
        except FileNotFound:
            await self._legacy.read_to_file(workspace_id, path, dest)  # raises if absent
            await self._primary.write_from_path(workspace_id, path, dest, None)  # backfill

    # ── listings → UNION ─────────────────────────────────────────────────────

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        p = await self._primary.ls(workspace_id, prefix)
        legacy = await self._legacy.ls(workspace_id, prefix)
        return list(dict.fromkeys([*p, *legacy]))

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        p = await self._primary.listdir(workspace_id, prefix)
        legacy = await self._legacy.listdir(workspace_id, prefix)
        return list(dict.fromkeys([*p, *legacy]))

    async def exists(self, workspace_id: str, path: str) -> bool:
        return await self._primary.exists(workspace_id, path) or await self._legacy.exists(
            workspace_id, path
        )

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        return await self._primary.is_dir(workspace_id, path) or await self._legacy.is_dir(
            workspace_id, path
        )

    async def stat_all(self, workspace_id: str, prefix: str = "") -> list[tuple[str, int]]:
        merged: dict[str, int] = {}
        for path, size in await self._legacy.stat_all(workspace_id, prefix):  # ty: ignore[unresolved-attribute]
            merged[path] = size
        for path, size in await self._primary.stat_all(workspace_id, prefix):  # ty: ignore[unresolved-attribute]
            merged[path] = size  # primary wins on a collision
        return list(merged.items())

    async def workspace_usage(self, workspace_id: str) -> int:
        merged: dict[str, int] = {}
        for path, size in await self._legacy.stat_all(workspace_id):  # ty: ignore[unresolved-attribute]
            merged[path] = size
        for path, size in await self._primary.stat_all(workspace_id):  # ty: ignore[unresolved-attribute]
            merged[path] = size
        return sum(merged.values())

    async def file_size(self, workspace_id: str, path: str) -> int | None:
        primary = await self._primary.file_size(workspace_id, path)  # ty: ignore[unresolved-attribute]
        if primary is not None:
            return primary
        return await self._legacy.file_size(workspace_id, path)  # ty: ignore[unresolved-attribute]

    async def census(self) -> dict[str, int]:
        # Telemetry only (#407); during migration this undercounts by whatever is
        # still legacy-only. That's acceptable — it's a trend gauge, not a
        # correctness signal — and it becomes exact once backfill completes.
        return await self._primary.census()  # ty: ignore[unresolved-attribute]

    # ── deletes → BOTH (so the union can't resurrect) ────────────────────────

    async def delete(self, workspace_id: str, path: str) -> None:
        await self._delete_from_both(workspace_id, path, "delete")

    async def rmdir(self, workspace_id: str, path: str) -> None:
        await self._delete_from_both(workspace_id, path, "rmdir")

    async def _delete_from_both(self, workspace_id: str, path: str, op: str) -> None:
        found = False
        for store in (self._primary, self._legacy):
            try:
                await getattr(store, op)(workspace_id, path)
                found = True
            except FileNotFound:
                pass
        if not found:
            raise FileNotFound(path)

    async def mkdir(self, workspace_id: str, path: str) -> None:
        # A file at path in legacy (not yet migrated) must block a dir, mirroring
        # primary.mkdir's own file-collision guard.
        if await self._legacy.exists(workspace_id, path):
            raise FileExists(path)
        await self._primary.mkdir(workspace_id, path)

    # ── backfill (sweeper unit of work) ──────────────────────────────────────

    async def backfill_workspace(self, workspace_id: str) -> int:
        """Copy every legacy file not yet in primary into primary; migrate empty
        dirs too. Idempotent — an already-present (newer) primary file is left
        untouched. Returns the number of files copied."""
        n = 0
        for path in await self._legacy.ls(workspace_id):
            if not await self._primary.exists(workspace_id, path):
                data = await self._legacy.read(workspace_id, path)
                await self._primary.write(workspace_id, path, data)
                n += 1
        for d in await self._legacy.listdir(workspace_id):
            if not await self._primary.is_dir(workspace_id, d):
                await self._primary.mkdir(workspace_id, d)
        return n
