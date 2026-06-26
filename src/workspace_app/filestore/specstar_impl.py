"""SpecstarFileStore — workspace files over per-file specstar ``Binary``
resources (issue #219).

Each file is ONE ``WorkspaceFile`` resource whose ``content`` is a ``Binary``
blob (bytes offloaded to the blob store; the record holds only file_id/size).
So a write is O(one file) — no whole-workspace read-modify-write, no inline
``dict[str, bytes]`` bloat, and big files never sit inline in the record. This
mirrors how KB ``SourceDoc`` / wiki ``WikiPage`` already store content.

``Binary`` restore is **eager** — ``restore_binary`` pulls a single file's
bytes into memory on demand — so the per-file shape makes "read one file = one
file's bytes" structural (you can't accidentally load the whole workspace).

Directories are pure strings (no bytes), so they stay in a small per-workspace
``_WorkspaceDirs`` record — the byte bottleneck is solved on the per-file side;
directory semantics (a write records its ancestor dirs; deleting a file leaves
parent dirs intact; empty dirs persist) are unchanged from the old design.
"""

from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import quote

from msgspec import Struct, field
from specstar import QB, SpecStar
from specstar.types import Binary, ResourceIDNotFoundError, RevisionStatus

from .protocol import FileExists, FileNotFound, dir_ancestors

_SLASH = "∕"  # division-slash look-alike (same convention as kb/doc_id.py)


class WorkspaceFile(Struct):
    """One workspace file. ``content`` is a Binary blob (bytes in the blob
    store, not inline). Resource id = ``{workspace_id}{path}`` slash-free."""

    workspace_id: str
    path: str
    content: Binary


class _WorkspaceDirs(Struct):
    """A workspace's directory paths (explicit + ancestor-of-a-file), kept in
    one small per-workspace record. Pure strings, no bytes — so the
    whole-workspace read-modify-write this incurs is O(#dirs), never O(bytes)."""

    workspace_id: str
    dirs: list[str] = field(default_factory=list)


def _wsid(workspace_id: str) -> str:
    """Deterministic resource id for a workspace's dir record: the workspace_id,
    percent-encoded slash-free. EVERY pod computes the same id, so any pod
    reads/writes the one shared record — the multi-pod invariant (#16)."""
    return quote(workspace_id, safe="")


def _fid(workspace_id: str, path: str) -> str:
    """Slash-free resource id for one file: the quoted workspace_id followed by
    the path with every ``/`` swapped for U+2215 (specstar ids can't hold an
    ASCII ``/``). The quoted prefix has no ``/`` and no U+2215, and ``path``
    starts with ``/`` → U+2215, so the boundary is unambiguous."""
    return quote(workspace_id, safe="") + path.replace("/", _SLASH)


class SpecstarFileStore:
    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec
        # Idempotent registration so two instances can share one store (the
        # multi-pod scenario + its test); a real pod registers once. Registered
        # by the store (not make_spec) so the memory-default app doesn't emit
        # these models' CRUD routes.
        with contextlib.suppress(ValueError):
            spec.add_model(WorkspaceFile, indexed_fields=["workspace_id"])
        with contextlib.suppress(ValueError):
            spec.add_model(_WorkspaceDirs)
        self._files = spec.get_resource_manager(WorkspaceFile)
        self._dirsmgr = spec.get_resource_manager(_WorkspaceDirs)

    # ── async wrappers (specstar I/O is sync) ────────────────────────────
    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, workspace_id, path, data)

    async def read(self, workspace_id: str, path: str) -> bytes:
        return await asyncio.to_thread(self._read_sync, workspace_id, path)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._ls_sync, workspace_id, prefix)

    async def exists(self, workspace_id: str, path: str) -> bool:
        return await asyncio.to_thread(self._files.exists, _fid(workspace_id, path))

    async def delete(self, workspace_id: str, path: str) -> None:
        await asyncio.to_thread(self._delete_sync, workspace_id, path)

    async def mkdir(self, workspace_id: str, path: str) -> None:
        await asyncio.to_thread(self._mkdir_sync, workspace_id, path)

    async def rmdir(self, workspace_id: str, path: str) -> None:
        await asyncio.to_thread(self._rmdir_sync, workspace_id, path)

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        return await asyncio.to_thread(lambda: path in self._dirs(workspace_id))

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(
            lambda: [d for d in self._dirs(workspace_id) if d.startswith(prefix)]
        )

    # ── files (per-resource Binary) ──────────────────────────────────────
    def _write_sync(self, workspace_id: str, path: str, data: bytes) -> None:
        rid = _fid(workspace_id, path)
        rec = WorkspaceFile(workspace_id=workspace_id, path=path, content=Binary(data=data))
        if self._files.exists(rid):
            self._files.modify(rid, rec, status=RevisionStatus.draft)
        else:
            self._files.create(rec, status=RevisionStatus.draft, resource_id=rid)
        ancestors = dir_ancestors(path)
        if ancestors:
            self._add_dirs(workspace_id, ancestors)

    def _read_sync(self, workspace_id: str, path: str) -> bytes:
        try:
            res = self._files.get(_fid(workspace_id, path))
        except ResourceIDNotFoundError as exc:
            raise FileNotFound(f"{workspace_id}:{path}") from exc
        data = self._files.restore_binary(res.data).content.data
        assert isinstance(data, bytes)
        return data

    def _ls_sync(self, workspace_id: str, prefix: str) -> list[str]:
        rows = self._files.list_resources((QB["workspace_id"] == workspace_id).build())
        return [
            r.data.path
            for r in rows
            if isinstance(r.data, WorkspaceFile) and r.data.path.startswith(prefix)
        ]

    def _delete_sync(self, workspace_id: str, path: str) -> None:
        rid = _fid(workspace_id, path)
        if not self._files.exists(rid):
            raise FileNotFound(f"{workspace_id}:{path}")
        # Hard delete — a removed file should vanish from ls. Parent dirs are
        # intentionally left intact (honest FS; `rmdir` removes a subtree).
        self._files.permanently_delete(rid)

    # ── directories (small per-workspace record) ─────────────────────────
    def _dirs(self, workspace_id: str) -> list[str]:
        try:
            rec = self._dirsmgr.get(_wsid(workspace_id)).data
        except ResourceIDNotFoundError:
            return []
        assert isinstance(rec, _WorkspaceDirs)
        return rec.dirs

    def _add_dirs(self, workspace_id: str, dirs: list[str]) -> None:
        rid = _wsid(workspace_id)
        try:
            rec = self._dirsmgr.get(rid).data
            assert isinstance(rec, _WorkspaceDirs)
            merged = sorted(set(rec.dirs) | set(dirs))
            if merged != rec.dirs:
                self._dirsmgr.modify(
                    rid,
                    _WorkspaceDirs(workspace_id=workspace_id, dirs=merged),
                    status=RevisionStatus.draft,
                )
        except ResourceIDNotFoundError:
            self._dirsmgr.create(
                _WorkspaceDirs(workspace_id=workspace_id, dirs=sorted(set(dirs))),
                status=RevisionStatus.draft,
                resource_id=rid,
            )

    def _set_dirs(self, workspace_id: str, dirs: list[str]) -> None:
        # Only ever called from rmdir, which has already confirmed `path` is in
        # the dir record — so the record provably exists; a plain modify.
        rid = _wsid(workspace_id)
        rec = _WorkspaceDirs(workspace_id=workspace_id, dirs=sorted(set(dirs)))
        self._dirsmgr.modify(rid, rec, status=RevisionStatus.draft)

    def _mkdir_sync(self, workspace_id: str, path: str) -> None:
        if self._files.exists(_fid(workspace_id, path)):
            raise FileExists(f"{workspace_id}:{path}")
        self._add_dirs(workspace_id, [path, *dir_ancestors(path)])

    def _rmdir_sync(self, workspace_id: str, path: str) -> None:
        dirs = self._dirs(workspace_id)
        if path not in dirs:
            raise FileNotFound(f"{workspace_id}:{path}")
        under = path + "/"
        self._set_dirs(workspace_id, [d for d in dirs if d != path and not d.startswith(under)])
        for p in [p for p in self._ls_sync(workspace_id, "") if p.startswith(under)]:
            self._files.permanently_delete(_fid(workspace_id, p))
