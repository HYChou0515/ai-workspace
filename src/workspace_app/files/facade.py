"""WorkspaceFiles — the single chokepoint for workspace file access.

It routes by **sandbox liveness**: when a sandbox dir is live for the workspace
(the single source of truth), reads/writes go there; when it's cold/recycled,
they fall back to the durable FileStore snapshot.

#345: with a shared per-item dir on one volume, the handle is derivable on ANY
pod (not just the one that woke the sandbox), so `_warm` PROBES the dir and
falls back to the snapshot only on `SandboxNotFound` (the dir is cold). That's
what keeps a read on a non-owning pod consistent with the live dir instead of
serving a stale snapshot — so workspace data no longer depends on sticky routing.

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

from ..filestore.protocol import FileExists, FileNotFound, FileStore
from ..sandbox.protocol import Sandbox, SandboxHandle, SandboxNotFound

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
        handle_for: Callable[[str], Awaitable[SandboxHandle | None]] | None = None,
        rebuild: Callable[[str], Awaitable[SandboxHandle]] | None = None,
    ) -> None:
        self._fs = filestore
        self._sb = sandbox
        # Async resolver: item → the handle its ONE live sandbox is reachable at,
        # or None when the item is globally cold (#492 same-source resolution).
        self._handle_for = handle_for
        # Async rebuild: item → a FRESH live handle when the resolved one turns out
        # reaped. Wired ONLY for a host-managed-durable (http) backend, where a
        # reaped-but-globally-warm item must NOT fall back to a cold durable write
        # (the host's `--delete` mirror would reconcile it away). None ⇒ the local
        # shared-vol backend, whose durable is the FileStore snapshot with no
        # host-side reconcile, so a cold dir safely falls back to durable (#345).
        self._rebuild = rebuild
        # Per-(workspace, path) lock so a compare-and-swap (read → check →
        # write) is atomic against other writers going through this facade.
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _warm(self, workspace_id: str) -> tuple[Sandbox, SandboxHandle] | None:
        """The item's ONE live sandbox, or None when it is globally cold (so the
        op uses the durable store). Reads AND writes route through here, so both
        hit the SAME source (#492) — a write never lands somewhere a later read
        wouldn't see, and never in a cold durable copy the host would reconcile
        away while a live sandbox exists.

        `handle_for` resolves the handle GLOBALLY (this pod's session / the shared
        address / the id-derived shared dir); None means globally cold (¬P) → the
        durable store. A resolved handle is probed for liveness:

        - alive ⇒ route the op to it.
        - `SandboxNotFound` (reaped/gone) with a `rebuild` wired (http) ⇒ rebuild
          from the durable archive and route to the fresh sandbox — NOT the cold
          durable store (the item is globally warm; a cold write would be lost).
        - `SandboxNotFound` with no rebuild (local shared-vol) ⇒ the shared dir is
          cold ⇒ fall back to the durable snapshot, as before (#345).
        - `SandboxBusy` (reachable but slow) propagates: the http client already
          retried with an escalating deadline, so this fails loud rather than
          rebuilding a live sandbox (split-brain) or cold-writing (data loss)."""
        if self._sb is None or self._handle_for is None:
            return None
        handle = await self._handle_for(workspace_id)
        if handle is None:
            return None
        try:
            await self._sb.exists(handle, "/")  # SandboxNotFound = gone; SandboxBusy propagates
        except SandboxNotFound:
            if self._rebuild is None:
                return None  # local shared-vol cold dir → durable snapshot (#345)
            handle = await self._rebuild(workspace_id)  # http: reaped but warm → rebuild
        return (self._sb, handle)

    async def read(self, workspace_id: str, path: str) -> bytes:
        path = _norm(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                return await sb.download(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        return await self._fs.read(workspace_id, path)

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        path = _norm(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            await sb.upload(h, data, path)
        else:
            await self._fs.write(workspace_id, path, data)

    async def create_exclusive(self, workspace_id: str, path: str, data: bytes) -> None:
        """Create-if-absent (#419 N1 numbering arbiter): raise `FileExists` if
        `path` is taken, else create it. Cold ⇒ the durable store's atomic
        create-only (`SpecstarFileStore.create_exclusive`). Warm ⇒ exists-check +
        upload against the live sandbox; that pair isn't a single atomic op, but a
        warm sandbox is single-pod (§N5) so the caller's per-type lock already
        serialises claimants there — the durable path is where cross-pod atomicity
        matters, and it has it."""
        path = _norm(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            if await sb.exists(h, path):
                raise FileExists(path)
            await sb.upload(h, data, path)
            return
        native = getattr(self._fs, "create_exclusive", None)
        if native is not None:
            await native(workspace_id, path, data)
            return
        if await self._fs.exists(workspace_id, path):
            raise FileExists(path)
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
        warm = await self._warm(workspace_id)
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
        warm = await self._warm(workspace_id)
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
        warm = await self._warm(workspace_id)
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
        warm = await self._warm(workspace_id)
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
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return [e.path for e in await sb.walk(h, prefix or "/")]
        return await self._fs.ls(workspace_id, prefix)

    async def stat_all(self, workspace_id: str, prefix: str = "") -> list[tuple[str, int]]:
        """Every file under ``prefix`` as ``(path, size)`` — WITHOUT reading a
        single file's bytes (#362). The file-tree endpoint only needs each
        file's size, and both routes already carry it as cheap metadata:

        - **warm**: ``walk`` returns ``FileEntry(path, size)`` (a stat, never a
          read), so a 600-file tree costs one directory traversal, not 600
          full-content downloads.
        - **cold**: the durable store exposes a batch ``stat_all`` (duck-typed,
          like ``file_size`` / ``workspace_usage``) that reads each record's
          inline ``size`` metadata, never restoring the offloaded blob.

        A store without that optimisation (an exotic backend) degrades to paths
        with an unknown size of 0 — still blob-free."""
        prefix = _norm(prefix) if prefix else prefix
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return [(e.path, e.size) for e in await sb.walk(h, prefix or "/")]
        batch = getattr(self._fs, "stat_all", None)
        if batch is not None:
            return await batch(workspace_id, prefix)
        return [(p, 0) for p in await self._fs.ls(workspace_id, prefix)]

    async def mkdir(self, workspace_id: str, path: str) -> None:
        path = _norm(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            await sb.mkdir(h, path)
        else:
            await self._fs.mkdir(workspace_id, path)

    async def rmdir(self, workspace_id: str, path: str) -> None:
        path = _norm(path)
        warm = await self._warm(workspace_id)
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
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            base = path.rstrip("/") + "/"
            return any(e.path.startswith(base) for e in await sb.walk(h, "/"))
        return await self._fs.is_dir(workspace_id, path)

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        prefix = _norm(prefix) if prefix else prefix
        warm = await self._warm(workspace_id)
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
        warm = await self._warm(workspace_id)
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
