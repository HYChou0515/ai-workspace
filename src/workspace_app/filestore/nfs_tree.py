"""NfsTreeFileStore — a FileStore backed by a real on-disk directory tree.

The #492 durable layer: workspace files live as ordinary files under
``{root}/{workspace_id}/{path}`` on a shared volume (NFS in production), so the
host can ``rsync`` a sandbox's working dir straight to the archive without any
per-file HTTP round-trip or DB write (the specstar-blob store's cost). Reads
that fall back to the durable snapshot (cold items) read the tree directly.

Unlike SpecstarFileStore this keeps NOTHING in a database — a file IS a file,
a directory IS a directory, ``delete`` is ``unlink``, ``usage`` is ``du``. That
is the whole point: the archive is a plain tree both this store and the host's
rsync write, agreeing on one path layout.

Isolation note (#492): this tree is the ARCHIVE, written by the app (and by the
root-running host via rsync). It is NOT the per-uid live sandbox dir, so it does
not need per-item ownership — files here may be owned by whoever wrote them; the
host chowns to the item uid only when it restores into the local live dir.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from .protocol import FileExists, FileNotFound

logger = logging.getLogger(__name__)


def _check_ws(workspace_id: str) -> str:
    """A workspace id names ONE directory segment under the root — reject
    anything that could traverse out of it (a separator, ``..``, or an absolute
    path). specstar ids are already slash-free, so this only ever fires on a
    programming error / hostile caller."""
    if workspace_id in ("", ".", "..") or "/" in workspace_id or "\\" in workspace_id:
        raise ValueError(f"unsafe workspace_id: {workspace_id!r}")
    return workspace_id


def _norm(path: str) -> str:
    p = path.removeprefix("./")
    return p if p.startswith("/") else "/" + p


class NfsTreeFileStore:
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def _item_root(self, workspace_id: str) -> Path:
        return self._root / _check_ws(workspace_id)

    def _abs(self, workspace_id: str, path: str) -> Path:
        """Resolve ``path`` under the item's dir, refusing any result that
        escapes it (``..`` traversal). Inner ``..`` that stays inside is fine."""
        item_root = self._item_root(workspace_id)
        rel = _norm(path).lstrip("/")
        target = os.path.normpath(item_root / rel)
        base = os.path.normpath(item_root)
        if target != base and not target.startswith(base + os.sep):
            raise ValueError(f"path escapes workspace: {path!r}")
        return Path(target)

    def _rel_of(self, item_root: Path, p: Path) -> str:
        """The workspace-relative, leading-slash path of ``p`` under an item."""
        return "/" + p.relative_to(item_root).as_posix()

    # ── writes ───────────────────────────────────────────────────────────────

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        target = self._abs(workspace_id, path)
        await asyncio.to_thread(self._write_sync, target, data)

    def _write_sync(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: write a sibling temp then rename over the target (same dir ⇒
        # same filesystem ⇒ os.replace is atomic; no reader sees a partial file).
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".wstmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            logger.warning("nfs_tree: write to %s failed, removed temp", target, exc_info=True)
            raise

    async def create_exclusive(self, workspace_id: str, path: str, data: bytes) -> None:
        target = self._abs(workspace_id, path)
        await asyncio.to_thread(self._create_exclusive_sync, target, data)

    def _create_exclusive_sync(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            logger.debug("nfs_tree: create_exclusive %s rejected, exists", target)
            raise FileExists(str(target)) from exc
        with os.fdopen(fd, "wb") as f:
            f.write(data)

    async def write_from_path(
        self, workspace_id: str, path: str, source: Path, content_type: str | None
    ) -> None:
        del content_type  # a plain tree stores bytes; no sniffing needed
        target = self._abs(workspace_id, path)
        await asyncio.to_thread(self._write_from_path_sync, target, source)

    def _write_from_path_sync(self, target: Path, source: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".wstmp-")
        os.close(fd)
        try:
            shutil.copyfile(source, tmp)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            logger.warning(
                "nfs_tree: write_from_path to %s failed, removed temp", target, exc_info=True
            )
            raise

    # ── reads ────────────────────────────────────────────────────────────────

    async def read(self, workspace_id: str, path: str) -> bytes:
        target = self._abs(workspace_id, path)
        return await asyncio.to_thread(self._read_sync, target, path)

    def _read_sync(self, target: Path, path: str) -> bytes:
        if not target.is_file():
            raise FileNotFound(path)
        return target.read_bytes()

    async def read_to_file(self, workspace_id: str, path: str, dest: Path) -> None:
        target = self._abs(workspace_id, path)
        await asyncio.to_thread(self._read_to_file_sync, target, dest, path)

    def _read_to_file_sync(self, target: Path, dest: Path, path: str) -> None:
        if not target.is_file():
            raise FileNotFound(path)
        shutil.copyfile(target, dest)

    async def exists(self, workspace_id: str, path: str) -> bool:
        target = self._abs(workspace_id, path)
        return await asyncio.to_thread(target.is_file)

    # ── listings ─────────────────────────────────────────────────────────────

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._ls_sync, workspace_id, prefix)

    def _ls_sync(self, workspace_id: str, prefix: str) -> list[str]:
        item_root = self._item_root(workspace_id)
        if not item_root.is_dir():
            return []
        out = [self._rel_of(item_root, p) for p in item_root.rglob("*") if p.is_file()]
        return [p for p in out if p.startswith(prefix)] if prefix else out

    async def stat_all(self, workspace_id: str, prefix: str = "") -> list[tuple[str, int]]:
        return await asyncio.to_thread(self._stat_all_sync, workspace_id, prefix)

    def _stat_all_sync(self, workspace_id: str, prefix: str) -> list[tuple[str, int]]:
        item_root = self._item_root(workspace_id)
        if not item_root.is_dir():
            return []
        out = [
            (self._rel_of(item_root, p), p.stat().st_size)
            for p in item_root.rglob("*")
            if p.is_file()
        ]
        return [t for t in out if t[0].startswith(prefix)] if prefix else out

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._listdir_sync, workspace_id, prefix)

    def _listdir_sync(self, workspace_id: str, prefix: str) -> list[str]:
        item_root = self._item_root(workspace_id)
        if not item_root.is_dir():
            return []
        out = [self._rel_of(item_root, p) for p in item_root.rglob("*") if p.is_dir()]
        return [p for p in out if p.startswith(prefix)] if prefix else out

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        target = self._abs(workspace_id, path)
        return await asyncio.to_thread(target.is_dir)

    # ── deletes ──────────────────────────────────────────────────────────────

    async def delete(self, workspace_id: str, path: str) -> None:
        target = self._abs(workspace_id, path)
        await asyncio.to_thread(self._delete_sync, target, path)

    def _delete_sync(self, target: Path, path: str) -> None:
        if not target.is_file():
            raise FileNotFound(path)
        target.unlink()  # parent dirs intentionally persist (honest FS)
        logger.debug("nfs_tree: deleted %s", target)

    async def mkdir(self, workspace_id: str, path: str) -> None:
        target = self._abs(workspace_id, path)
        await asyncio.to_thread(self._mkdir_sync, target, path)

    def _mkdir_sync(self, target: Path, path: str) -> None:
        if target.is_file():
            raise FileExists(path)
        target.mkdir(parents=True, exist_ok=True)

    async def rmdir(self, workspace_id: str, path: str) -> None:
        target = self._abs(workspace_id, path)
        await asyncio.to_thread(self._rmdir_sync, target, path)

    def _rmdir_sync(self, target: Path, path: str) -> None:
        if not target.is_dir():
            raise FileNotFound(path)
        shutil.rmtree(target)
        logger.debug("nfs_tree: rmdir removed subtree %s", target)

    # ── usage / census (#245 / #407) ─────────────────────────────────────────

    async def workspace_usage(self, workspace_id: str) -> int:
        return await asyncio.to_thread(self._usage_sync, workspace_id)

    def _usage_sync(self, workspace_id: str) -> int:
        item_root = self._item_root(workspace_id)
        if not item_root.is_dir():
            return 0
        return sum(p.stat().st_size for p in item_root.rglob("*") if p.is_file())

    async def file_size(self, workspace_id: str, path: str) -> int | None:
        target = self._abs(workspace_id, path)
        return await asyncio.to_thread(self._file_size_sync, target)

    def _file_size_sync(self, target: Path) -> int | None:
        return target.stat().st_size if target.is_file() else None

    async def census(self) -> dict[str, int]:
        return await asyncio.to_thread(self._census_sync)

    def _census_sync(self) -> dict[str, int]:
        counts: list[int] = []
        if self._root.is_dir():
            for item_root in self._root.iterdir():
                if item_root.is_dir():
                    n = sum(1 for p in item_root.rglob("*") if p.is_file())
                    if n:
                        counts.append(n)
        return {
            "total_workspacefile_rows": sum(counts),
            "n_workspaces": len(counts),
            "max_files_per_ws": max(counts, default=0),
        }
