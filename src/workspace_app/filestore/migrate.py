"""One-time migration: inline-bytes workspace files → per-file ``Binary``
(issue #219).

The old ``SpecstarFileStore`` kept every file of a workspace inline in one
``_WorkspaceFiles.files: dict[str, bytes]`` record. This rewrites those records
into the new per-file ``WorkspaceFile`` (``Binary`` blob) + ``_WorkspaceDirs``
shape. Run once per deploy before the new store serves traffic (the new store no
longer registers ``_WorkspaceFiles``, so legacy rows are otherwise unreadable).

The legacy struct MUST keep the class name ``_WorkspaceFiles`` — specstar maps a
model to its stored rows by the model name, so a renamed struct would read an
empty (different) table.
"""

from __future__ import annotations

import contextlib
import logging

from msgspec import Struct, field
from specstar import SpecStar

from .specstar_impl import SpecstarFileStore

logger = logging.getLogger(__name__)


class _WorkspaceFiles(Struct):
    """The pre-#219 inline-bytes shape — registered only to read legacy rows."""

    workspace_id: str
    files: dict[str, bytes]
    dirs: list[str] = field(default_factory=list)


def migrate_inline_to_binary(spec: SpecStar) -> int:
    """Convert every legacy ``_WorkspaceFiles`` record into per-file
    ``WorkspaceFile`` Binary resources (+ the small ``_WorkspaceDirs`` record),
    then permanently delete the legacy record. Returns the number of workspaces
    migrated. Idempotent: once the legacy rows are consumed, a re-run finds
    nothing and returns 0."""
    with contextlib.suppress(ValueError):
        spec.add_model(_WorkspaceFiles)
    rm_old = spec.get_resource_manager(_WorkspaceFiles)
    store = SpecstarFileStore(spec)
    migrated = 0
    for r in rm_old.list_resources():
        rec = r.data
        assert isinstance(rec, _WorkspaceFiles)  # narrows Struct|Unset for ty
        for path, data in rec.files.items():
            store._write_sync(rec.workspace_id, path, data)
        if rec.dirs:
            store._add_dirs(rec.workspace_id, rec.dirs)
        rm_old.permanently_delete(r.info.resource_id)  # ty: ignore[unresolved-attribute]
        logger.debug("migrate: workspace %s migrated (%d files)", rec.workspace_id, len(rec.files))
        migrated += 1
    logger.info("migrate: inline->binary migration complete, %d workspaces", migrated)
    return migrated
