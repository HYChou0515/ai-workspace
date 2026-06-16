"""WikiFileStore — a FileStore-protocol backend for a collection's LLM
wiki (issue #50), one ``WikiPage`` resource per page.

Why not reuse ``SpecstarFileStore``: that keeps a whole workspace's files
inline in ONE resource, so editing any page reads+writes the entire wiki
and stamps a full-workspace revision. The wiki grows unbounded and is
re-edited on every ingest, so that write amplification + revision bloat
is the design's worst bottleneck (plan §5①). Here:

  - **per-page**: each ``(collection_id, path)`` is its own resource →
    editing a page is O(page), and ``ls`` is an indexed query.
  - **draft writes**: ``write`` uses specstar ``modify()`` on a draft
    revision (mutate in place) instead of ``update()`` (new revision),
    so high-churn machine edits don't bloat revision history.

The wiki agents reuse the existing file tools unchanged — they just get a
context whose ``filestore`` is a ``WikiFileStore`` and whose
``workspace_id`` is the collection id. Directories are implicit (derived
from page paths); the wiki doesn't need empty folders.
"""

from __future__ import annotations

import asyncio

from specstar import QB, SpecStar
from specstar.types import (
    Binary,
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    RevisionStatus,
)

from ...filestore.protocol import FileNotFound, dir_ancestors
from ...resources import WikiPage

_SLASH = "∕"  # division-slash look-alike (same convention as kb/doc_id.py)


def _rid(collection_id: str, path: str) -> str:
    """Slash-free resource id for one wiki page (specstar ids can't hold
    ASCII ``/``). The natural key ``{collection_id}{path}`` with every
    ``/`` swapped for U+2215."""
    return f"{collection_id}{path}".replace("/", _SLASH)


class WikiFileStore:
    """FileStore over per-page ``WikiPage`` resources. ``workspace_id`` is
    the collection id."""

    def __init__(self, spec: SpecStar) -> None:
        self._rm = spec.get_resource_manager(WikiPage)

    # ── audit user (issue #83) ───────────────────────────────────────
    def acting_as(self, user: str):
        """Stamp every wiki-page write inside the block as ``user``. The wiki
        runs on a job queue, and a job pod has NO request user — so page writes
        would otherwise be credited to the bare worker default. ``using`` binds
        this store's single ``WikiPage`` manager instance, which all writes go
        through, so the whole maintainer run is covered."""
        return self._rm.using(user=user)

    # ── reads ────────────────────────────────────────────────────────
    def _paths(self, collection_id: str) -> list[str]:
        return [
            r.data.path
            for r in self._rm.list_resources((QB["collection_id"] == collection_id).build())
        ]

    async def read(self, workspace_id: str, path: str) -> bytes:
        return await asyncio.to_thread(self._read_sync, workspace_id, path)

    def _read_sync(self, workspace_id: str, path: str) -> bytes:
        try:
            page = self._rm.restore_binary(self._rm.get(_rid(workspace_id, path)).data)
        except ResourceIDNotFoundError as exc:
            raise FileNotFound(f"{workspace_id}:{path}") from exc
        data = page.content.data
        assert isinstance(data, bytes)
        return data

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(
            lambda: [p for p in self._paths(workspace_id) if p.startswith(prefix)]
        )

    async def exists(self, workspace_id: str, path: str) -> bool:
        return await asyncio.to_thread(self._rm.exists, _rid(workspace_id, path))

    # ── writes (draft modify → no revision bloat) ────────────────────
    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, workspace_id, path, data)

    def _write_sync(self, workspace_id: str, path: str, data: bytes) -> None:
        rid = _rid(workspace_id, path)
        page = WikiPage(collection_id=workspace_id, path=path, content=Binary(data=data))
        if self._rm.exists(rid):
            # Mutate the draft in place — no new revision (plan §5①b).
            self._rm.modify(rid, page, status=RevisionStatus.draft)
        else:
            self._rm.create(page, status=RevisionStatus.draft, resource_id=rid)

    # ── optimistic concurrency (CAS) — cross-worker-safe edits ───────
    # specstar v0.11.6: `etag` bumps on every write *including* in-place
    # draft modify(), so it catches concurrent in-place edits that
    # `expected_revision_id` alone would miss. `WorkspaceFiles.edit`
    # duck-types these two methods to run an edit→retry loop that is safe
    # across processes (two ingest workers folding the same collection).
    async def read_with_etag(self, workspace_id: str, path: str) -> tuple[bytes, str] | None:
        """One atomic read of (content, concurrency token), or None if the page
        doesn't exist. The token is passed back to ``write_cas``."""
        return await asyncio.to_thread(self._read_with_etag_sync, workspace_id, path)

    def _read_with_etag_sync(self, workspace_id: str, path: str) -> tuple[bytes, str] | None:
        try:
            res = self._rm.get(_rid(workspace_id, path))
        except ResourceIDNotFoundError:
            return None
        data = self._rm.restore_binary(res.data).content.data
        assert isinstance(data, bytes)
        return data, res.info.etag

    async def write_cas(
        self, workspace_id: str, path: str, data: bytes, expected_etag: str | None
    ) -> bool:
        """Write only if the page still matches ``expected_etag`` (None ⇒ the
        page must not exist yet). Returns True on success, False when a
        concurrent writer won the race — the caller re-reads and retries."""
        return await asyncio.to_thread(
            self._write_cas_sync, workspace_id, path, data, expected_etag
        )

    def _write_cas_sync(
        self, workspace_id: str, path: str, data: bytes, expected_etag: str | None
    ) -> bool:
        rid = _rid(workspace_id, path)
        page = WikiPage(collection_id=workspace_id, path=path, content=Binary(data=data))
        if expected_etag is None:
            try:
                # if_not_exists / expected_etag are on the concrete manager
                # (specstar v0.11.6) but not yet on the IResourceManager ABC stub.
                self._rm.create(
                    page,
                    status=RevisionStatus.draft,
                    resource_id=rid,
                    if_not_exists=True,  # ty: ignore[unknown-argument]
                )
                return True
            except DuplicateResourceError:
                return False
        try:
            self._rm.modify(
                rid,
                page,
                status=RevisionStatus.draft,
                expected_etag=expected_etag,  # ty: ignore[unknown-argument]
            )
            return True
        except (PreconditionFailedError, ResourceIDNotFoundError):
            return False

    async def clear(self, workspace_id: str) -> int:
        """Permanently delete every page in the collection's wiki (the admin
        wipe handle). Returns how many pages were removed."""
        return await asyncio.to_thread(self._clear_sync, workspace_id)

    def _clear_sync(self, workspace_id: str) -> int:
        n = 0
        for r in self._rm.list_resources((QB["collection_id"] == workspace_id).build()):
            self._rm.permanently_delete(r.info.resource_id)  # ty: ignore[unresolved-attribute]
            n += 1
        return n

    async def delete(self, workspace_id: str, path: str) -> None:
        await asyncio.to_thread(self._delete_sync, workspace_id, path)

    def _delete_sync(self, workspace_id: str, path: str) -> None:
        rid = _rid(workspace_id, path)
        if not self._rm.exists(rid):
            raise FileNotFound(f"{workspace_id}:{path}")
        # Hard delete — a removed wiki page should vanish from ls (no
        # undelete needed). `delete` is a soft delete that lingers in
        # list_resources; `permanently_delete` is what the doc/chunk
        # delete paths use too.
        self._rm.permanently_delete(rid)

    # ── directories (implicit, derived from page paths) ──────────────
    async def mkdir(self, workspace_id: str, path: str) -> None:
        # No-op: a directory exists once a page lives under it. The wiki
        # never needs an empty folder.
        return None

    async def rmdir(self, workspace_id: str, path: str) -> None:
        base = path.rstrip("/") + "/"
        for p in await asyncio.to_thread(self._paths, workspace_id):
            if p.startswith(base):
                await self.delete(workspace_id, p)

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        base = path.rstrip("/") + "/"
        return await asyncio.to_thread(
            lambda: any(p.startswith(base) for p in self._paths(workspace_id))
        )

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        def _dirs() -> list[str]:
            seen: set[str] = set()
            for p in self._paths(workspace_id):
                seen.update(dir_ancestors(p))
            return sorted(d for d in seen if d.startswith(prefix))

        return await asyncio.to_thread(_dirs)
