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
from pathlib import Path
from typing import Any
from urllib.parse import quote

from msgspec import Struct, field
from specstar import QB, Schema, SpecStar, Sum
from specstar.types import Binary, IndexableField, ResourceIDNotFoundError, RevisionStatus

from .protocol import FileExists, FileNotFound, dir_ancestors

_SLASH = "∕"  # division-slash look-alike (same convention as kb/doc_id.py)
_CHUNK = 8 * 1024 * 1024  # 8 MB — read a source file into blob parts a chunk at a time


def _reindex_only(record: Any) -> Any:
    """No-op migration step (record unchanged) used as ``step(None,
    _reindex_only, …)``: migrating a pre-``content_size`` row (version ``None``)
    to ``v2`` re-extracts its indexed_data — the backfill — without touching the
    record. Mirrors ``resources._reindex_only`` (kept local so filestore doesn't
    depend on the resources package)."""
    return record


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
            # `content.size` is indexed (as the scalar `content_size`, mirroring
            # SourceDoc — a dotted index key trips SQL backends) so per-workspace
            # usage is one Sum aggregate (#245). The Binary keeps `size` inline in
            # the record (only bytes are offloaded), so this needs no extra field;
            # old rows backfill via the migrate route (`rm.migrate` re-extracts it).
            spec.add_model(
                Schema(WorkspaceFile, "v2").step(None, _reindex_only, source_type=WorkspaceFile),
                indexed_fields=[
                    "workspace_id",
                    IndexableField("content.size", index_key="content_size"),
                ],
            )
        with contextlib.suppress(ValueError):
            spec.add_model(_WorkspaceDirs)
        self._files = spec.get_resource_manager(WorkspaceFile)
        self._dirsmgr = spec.get_resource_manager(_WorkspaceDirs)

    # ── async wrappers (specstar I/O is sync) ────────────────────────────
    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, workspace_id, path, data)

    async def write_from_path(
        self, workspace_id: str, path: str, source: Path, content_type: str | None
    ) -> None:
        """Stream a file's bytes from ``source`` into the blob store a chunk at
        a time (never the whole file in RAM), then point a ``WorkspaceFile`` at
        the resulting blob. The big-file ingest path — the upload endpoint
        streams the request body to ``source`` first."""
        await asyncio.to_thread(
            self._write_from_path_sync, workspace_id, path, source, content_type
        )

    async def read(self, workspace_id: str, path: str) -> bytes:
        return await asyncio.to_thread(self._read_sync, workspace_id, path)

    async def read_to_file(self, workspace_id: str, path: str, dest: Path) -> None:
        """Stream a file's bytes out to ``dest`` a chunk at a time (never the
        whole file in RAM) — the restore path uses this to seed a sandbox."""
        await asyncio.to_thread(self._read_to_file_sync, workspace_id, path, dest)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._ls_sync, workspace_id, prefix)

    async def exists(self, workspace_id: str, path: str) -> bool:
        return await asyncio.to_thread(self._files.exists, _fid(workspace_id, path))

    async def workspace_usage(self, workspace_id: str) -> int:
        """Total logical bytes the workspace's files occupy — the sum of every
        live ``WorkspaceFile``'s ``content.size`` (#245). Content-addressed
        dedup means physical ≤ this, so it's a conservative quota basis. One
        indexed ``Sum`` aggregate scoped to the workspace, never a fetch-all."""
        return await asyncio.to_thread(self._workspace_usage_sync, workspace_id)

    async def file_size(self, workspace_id: str, path: str) -> int | None:
        """Size of one file from its record's ``content.size`` — a point read,
        never the blob bytes (so an overwrite quota check is cheap). None if the
        file doesn't exist."""
        return await asyncio.to_thread(self._file_size_sync, workspace_id, path)

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
    def _put_record(self, workspace_id: str, path: str, content: Binary) -> None:
        """Create-or-overwrite the ``WorkspaceFile`` at ``path`` with ``content``
        (a Binary holding either inline bytes or a finalized blob ``file_id``),
        and record the path's ancestor dirs. Draft ``modify`` so high-churn
        rewrites don't bloat revision history."""
        rid = _fid(workspace_id, path)
        rec = WorkspaceFile(workspace_id=workspace_id, path=path, content=content)
        if self._files.exists(rid):
            self._files.modify(rid, rec, status=RevisionStatus.draft)
        else:
            self._files.create(rec, status=RevisionStatus.draft, resource_id=rid)
        ancestors = dir_ancestors(path)
        if ancestors:
            self._add_dirs(workspace_id, ancestors)

    def _write_sync(self, workspace_id: str, path: str, data: bytes) -> None:
        self._put_record(workspace_id, path, Binary(data=data))

    def _write_from_path_sync(
        self, workspace_id: str, path: str, source: Path, content_type: str | None
    ) -> None:
        if source.stat().st_size == 0:
            # finalize rejects a session with no parts; an empty file is inline.
            self._put_record(workspace_id, path, Binary(data=b""))
            return
        bs = self._files.blob_store  # ty: ignore[unresolved-attribute]
        session = bs.create_upload_session(
            key=None, content_type=content_type, size=None, total_parts=None
        )
        part = 0
        with open(source, "rb") as f:
            while chunk := f.read(_CHUNK):
                part += 1
                bs.upload_to_session(session.upload_id, chunk, part_number=part)
        self._put_record(workspace_id, path, bs.finalize_upload_session(session.upload_id))

    def _read_sync(self, workspace_id: str, path: str) -> bytes:
        try:
            res = self._files.get(_fid(workspace_id, path))
        except ResourceIDNotFoundError as exc:
            raise FileNotFound(f"{workspace_id}:{path}") from exc
        data = self._files.restore_binary(res.data).content.data
        assert isinstance(data, bytes)
        return data

    def _read_to_file_sync(self, workspace_id: str, path: str, dest: Path) -> None:
        try:
            res = self._files.get(_fid(workspace_id, path))
        except ResourceIDNotFoundError as exc:
            raise FileNotFound(f"{workspace_id}:{path}") from exc
        assert isinstance(res.data, WorkspaceFile)
        file_id = res.data.content.file_id
        bs = self._files.blob_store  # ty: ignore[unresolved-attribute]
        info = bs.get_stream(file_id) if isinstance(file_id, str) else None
        if info is None:
            # Backend without streaming (in-memory) or an inline/empty blob:
            # fall back to a single restore. Big files only land on a disk blob
            # store, whose get_stream is the streaming path below.
            dest.write_bytes(self._read_sync(workspace_id, path))
            return
        with open(dest, "wb") as f:
            for chunk in info.iterator:
                f.write(chunk)

    def _workspace_usage_sync(self, workspace_id: str) -> int:
        rows = self._files.exp_aggregate_by(  # ty: ignore[unresolved-attribute]
            by=QB["workspace_id"],
            aggregates={"used": Sum(QB["content_size"])},
            query=(QB["workspace_id"] == workspace_id).build(),
        )
        # No rows ⇒ no group ⇒ 0. A group whose rows all pre-date the
        # `content_size` index (written before #245, not yet migrated) sums to
        # None — it under-counts as 0 until the operator runs migrate/execute.
        used = rows[0]["used"] if rows else None
        return int(used) if used is not None else 0

    def _file_size_sync(self, workspace_id: str, path: str) -> int | None:
        try:
            res = self._files.get(_fid(workspace_id, path))
        except ResourceIDNotFoundError:
            return None
        assert isinstance(res.data, WorkspaceFile)
        size = res.data.content.size  # always set on a stored Binary
        assert isinstance(size, int)
        return size

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
