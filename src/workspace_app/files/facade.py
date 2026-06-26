"""WorkspaceFiles — the single chokepoint for workspace file access.

It routes by **sandbox liveness**: when a sandbox is up for the workspace (the
single source of truth), reads/writes go there; when it's cold, they fall back
to the FileStore snapshot. A cold sandbox is frozen, so the snapshot is its
exact image — there's never a moment where two writable sources diverge.

`is_dir`/`listdir` are derived from `walk` when warm (the Sandbox Protocol has
no native dir listing); cold, they read the FileStore which tracks dirs
first-class. Constructed without a sandbox (`sandbox=None`), it degrades to a
plain FileStore pass-through — handy for tests + the transitional fallback.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..filestore.protocol import FileNotFound, FileStore
from ..sandbox.protocol import Sandbox, SandboxHandle

# How many times an etag-guarded edit re-bases against a concurrent writer
# before giving up and reporting a conflict. A handful is plenty — contention
# on one wiki page across workers is rare and each retry re-reads fresh.
_CAS_EDIT_RETRIES = 5


def _norm(path: str) -> str:
    """Canonicalise a workspace path: ``./brief.md``, ``brief.md`` and
    ``/brief.md`` all map to the same internal key ``/brief.md``. So
    the agent can write whichever feels natural in prose and the
    underlying store stays consistent."""
    p = path.removeprefix("./")
    return p if p.startswith("/") else "/" + p


class WorkspaceFiles:
    def __init__(
        self,
        filestore: FileStore,
        sandbox: Sandbox | None = None,
        handle_for: Callable[[str], SandboxHandle | None] | None = None,
    ) -> None:
        self._fs = filestore
        self._sb = sandbox
        self._handle_for = handle_for
        # Per-(workspace, path) lock so a compare-and-swap (read → check →
        # write) is atomic against other writers going through this facade.
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def _warm(self, workspace_id: str) -> tuple[Sandbox, SandboxHandle] | None:
        """The live sandbox for this workspace, or None when it's cold."""
        if self._sb is None or self._handle_for is None:
            return None
        handle = self._handle_for(workspace_id)
        return (self._sb, handle) if handle is not None else None

    async def read(self, workspace_id: str, path: str) -> bytes:
        path = _norm(path)
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                return await sb.download(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        return await self._fs.read(workspace_id, path)

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        path = _norm(path)
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            await sb.upload(h, data, path)
        else:
            await self._fs.write(workspace_id, path, data)

    async def write_from_path(
        self, workspace_id: str, path: str, source: Path, content_type: str | None = None
    ) -> None:
        """Like `write`, but the content is a staged on-disk file `source` that
        is streamed into its destination — so a big upload never sits whole in
        RAM (issue #219). Warm ⇒ stream straight into the live sandbox (the
        snapshot catches up on the next mirror, exactly like any warm write);
        cold ⇒ stream into the FileStore blob."""
        path = _norm(path)
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            await sb.upload_file(h, source, path)
        else:
            await self._fs.write_from_path(workspace_id, path, source, content_type)

    async def read_to_file(self, workspace_id: str, path: str, dest: Path) -> None:
        """Like `read`, but stream the bytes out to the on-disk `dest` — RAM-free
        for big files (issue #219). Routes warm→sandbox / cold→snapshot like
        `read`; a missing file maps to `FileNotFound`."""
        path = _norm(path)
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                await sb.download_to_file(h, path, dest)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        else:
            await self._fs.read_to_file(workspace_id, path, dest)

    async def exists(self, workspace_id: str, path: str) -> bool:
        path = _norm(path)
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return await sb.exists(h, path)
        return await self._fs.exists(workspace_id, path)

    async def workspace_usage(self, workspace_id: str) -> int:
        """Total durable bytes the workspace's files occupy — the #245 quota
        basis. Always the **durable** store (the disk the quota protects), never
        the warm sandbox, so the usage bar and the quota agree on one number. A
        store without usage accounting (e.g. the wiki-page store) reports 0 —
        duck-typed like the CAS pair."""
        usage = getattr(self._fs, "workspace_usage", None)
        return await usage(workspace_id) if usage is not None else 0

    async def file_size(self, workspace_id: str, path: str) -> int | None:
        """Durable size of one file (None if absent) — the overwrite credit for a
        quota check. Durable store, mirroring `workspace_usage`."""
        size = getattr(self._fs, "file_size", None)
        return await size(workspace_id, _norm(path)) if size is not None else None

    async def remaining_quota(self, workspace_id: str, path: str, quota: int) -> int | None:
        """Bytes the file at `path` may occupy before the workspace hits `quota`
        — the headroom the upload/edit endpoints gate on (#245). An overwrite is
        a *replace*: the existing file's size is credited back, so re-uploading a
        same-size file never falsely rejects. `quota` of 0 disables the cap →
        None (no limit). Always measured against the **durable** store (the disk
        the quota protects), never the warm sandbox — so the sandbox mirror,
        which writes the raw store directly, is intentionally not gated (#245
        choice B)."""
        if not quota:
            return None
        used = await self.workspace_usage(workspace_id)
        old = await self.file_size(workspace_id, path) or 0
        return quota - (used - old)

    async def delete(self, workspace_id: str, path: str) -> None:
        path = _norm(path)
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                await sb.delete(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        else:
            await self._fs.delete(workspace_id, path)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        prefix = _norm(prefix) if prefix else prefix
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return [e.path for e in await sb.walk(h, prefix or "/")]
        return await self._fs.ls(workspace_id, prefix)

    async def mkdir(self, workspace_id: str, path: str) -> None:
        path = _norm(path)
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            await sb.mkdir(h, path)
        else:
            await self._fs.mkdir(workspace_id, path)

    async def rmdir(self, workspace_id: str, path: str) -> None:
        path = _norm(path)
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                await sb.rmdir(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        else:
            await self._fs.rmdir(workspace_id, path)

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        path = _norm(path)
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            base = path.rstrip("/") + "/"
            return any(e.path.startswith(base) for e in await sb.walk(h, "/"))
        return await self._fs.is_dir(workspace_id, path)

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        prefix = _norm(prefix) if prefix else prefix
        warm = self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            dirs: set[str] = set()
            for e in await sb.walk(h, prefix or "/"):
                parts = e.path.strip("/").split("/")
                for i in range(1, len(parts)):
                    dirs.add("/" + "/".join(parts[:i]))
            return sorted(dirs)
        return await self._fs.listdir(workspace_id, prefix)

    # ---- compare-and-swap writes (the agent must declare its expectation) ----

    async def create(self, workspace_id: str, path: str, data: bytes) -> bytes | None:
        """Create-only write: succeed (return None) if `path` doesn't exist;
        otherwise don't clobber — return the current bytes so the caller can
        decide. Atomic under the per-path lock."""
        path = _norm(path)
        async with self._locks[(workspace_id, path)]:
            if await self.exists(workspace_id, path):
                return await self.read(workspace_id, path)
            await self.write(workspace_id, path, data)
            return None

    async def edit(self, workspace_id: str, path: str, old: str, new: str) -> str | None:
        """Replace the **unique** occurrence of `old` with `new`. Succeed
        (return None) only when `old` appears exactly once; otherwise it's a
        conflict (missing file, text not found, or ambiguous) and the current
        text is returned so the caller can re-base. Atomic under the per-path
        lock — so a concurrent change makes `old` stop matching and the edit is
        rejected rather than blindly applied.

        When the file store exposes optimistic-concurrency hooks
        (``read_with_etag`` + ``write_cas``) and no live sandbox owns the
        workspace, the read→write is additionally guarded by the store's etag,
        so the edit is safe against writers in *other processes* (e.g. a second
        ingest worker), not just other coroutines — the per-path lock only
        covers this process."""
        path = _norm(path)
        warm = self._warm(workspace_id)
        write_cas = getattr(self._fs, "write_cas", None)
        read_with_etag = getattr(self._fs, "read_with_etag", None)
        async with self._locks[(workspace_id, path)]:
            if warm is None and write_cas is not None and read_with_etag is not None:
                return await self._edit_cas(workspace_id, path, old, new, write_cas, read_with_etag)
            try:
                current = (await self.read(workspace_id, path)).decode("utf-8", errors="replace")
            except FileNotFound:
                return ""
            if current.count(old) != 1:
                return current
            await self.write(workspace_id, path, current.replace(old, new, 1).encode("utf-8"))
            return None

    async def _edit_cas(
        self,
        workspace_id: str,
        path: str,
        old: str,
        new: str,
        write_cas: Callable[[str, str, bytes, str | None], Awaitable[bool]],
        read_with_etag: Callable[[str, str], Awaitable[tuple[bytes, str] | None]],
    ) -> str | None:
        """Etag-guarded edit→retry: re-read on every attempt so a concurrent
        write makes us re-base off the latest content instead of clobbering it."""
        for _ in range(_CAS_EDIT_RETRIES):
            got = await read_with_etag(workspace_id, path)
            if got is None:
                return ""  # the page doesn't exist — re-create it with write_file
            data, etag = got
            current = data.decode("utf-8", errors="replace")
            if current.count(old) != 1:
                return current  # text conflict — caller re-reads and re-bases
            applied = await write_cas(
                workspace_id, path, current.replace(old, new, 1).encode("utf-8"), etag
            )
            if applied:
                return None
            # A concurrent writer bumped the etag between our read and write —
            # loop to re-read and re-apply against the new content.
        # Persistent contention: hand back the latest content as a conflict.
        got = await read_with_etag(workspace_id, path)
        return got[0].decode("utf-8", errors="replace") if got is not None else ""
