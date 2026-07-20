"""SandboxSync — moves files between the live Sandbox (the single source of
truth while warm) and the FileStore snapshot (durable backup + restore source).

Two operations now that the sandbox is authoritative (sandbox-as-SoT redesign):

- restore: pull every snapshot path into a freshly-woken sandbox so the agent's
  shell starts with the files it left behind, and seed the diff state.
- mirror:  PULL the live sandbox into the snapshot — copy files whose opaque
  `version` changed since the last mirror, and delete snapshot files the
  sandbox no longer has. A complete, deletion-aware mirror (so a restarted
  sandbox restores the exact last state). Driven on a ≤window throttle while a
  turn is active + forced at turn-end / idle-kill / close.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from ..filestore.protocol import FileStore
from ..sandbox.protocol import Sandbox, SandboxHandle, SandboxNotFound
from .ignore import DEFAULT_IGNORES, should_ignore

if TYPE_CHECKING:
    from ..monitor import IMonitor


logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _staging_file() -> Iterator[Path]:
    """A short-lived on-disk hand-off file for streaming one file between the
    sandbox and the FileStore — so neither side holds the whole file in RAM
    (issue #219). Removed on exit."""
    fd, name = tempfile.mkstemp(prefix="wsfile-")
    os.close(fd)
    tmp = Path(name)
    try:
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)


class SandboxSync:
    def __init__(
        self,
        filestore: FileStore,
        sandbox: Sandbox,
        *,
        ignores: list[str] | None = None,
        monitor: IMonitor | None = None,
        on_measured: Callable[[str, dict[str, int]], None] | None = None,
    ) -> None:
        self._fs = filestore
        self._sb = sandbox
        self._ignores = list(ignores) if ignores is not None else list(DEFAULT_IGNORES)
        # Per-workspace {path: version} last mirrored to the snapshot — the diff
        # state so `mirror` only re-copies changed files and can spot deletions.
        self._versions: dict[str, dict[str, str]] = {}
        # #407: optional telemetry sink. One SUMMARY event per mirror/restore
        # call (never per file — the monitor assumes a few events per turn), so
        # we can measure durable-store cost (file counts + I/O) before deciding
        # whether the per-file model needs a cheaper (archive/batched) rewrite.
        self._monitor = monitor
        # #538: the quota's measurement sink. `mirror` already walks the whole
        # workspace on its own cadence, so handing the sizes over is free — and
        # it is what keeps the walk off the request path, where a lazily-measured
        # quota made whichever user asked first pay for the traversal and see its
        # errors. The sizes are the post-`should_ignore` set this mirror actually
        # persists, so the quota counts exactly what reaches the durable store.
        self._on_measured = on_measured

    def _record(self, kind: str, group_id: str, started: float, **fields: int) -> None:
        """Emit one telemetry summary event for a just-finished sync op. A no-op
        when no monitor is wired (tests / minimal apps)."""
        if self._monitor is None:
            return
        self._monitor.record(
            {
                "kind": kind,
                "group_id": group_id,
                "t": int(time.time() * 1000),  # wall-clock, for the summary's time window
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                **fields,
            }
        )

    async def restore(
        self,
        workspace_id: str,
        handle: SandboxHandle,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        started = time.monotonic()
        n = 0
        n_bytes = 0
        paths = await self._fs.ls(workspace_id)
        total = len(paths)
        logger.debug("sync: restore workspace %s: %d snapshot paths", workspace_id, total)
        # #492 P11: stream (done, total) so a slow cold wake shows "還原中 N/M"
        # instead of a blank running card. A leading 0/total makes the card
        # appear immediately with the fraction known upfront; then one tick per
        # restored file. An empty workspace wakes instantly → no frame at all.
        if on_progress is not None and total:
            on_progress(0, total)
        for path in paths:
            # Stream FileStore → sandbox through a staging file so a big file
            # never sits whole in RAM on wake (issue #219).
            with _staging_file() as tmp:
                await self._fs.read_to_file(workspace_id, path, tmp)
                await self._sb.upload_file(handle, tmp, path)
                n_bytes += tmp.stat().st_size
            n += 1
            if on_progress is not None:
                on_progress(n, total)
        logger.info("sync: restored %d files, %d bytes (workspace %s)", n, n_bytes, workspace_id)
        # Seed the diff state from the just-restored sandbox so the first mirror
        # after a wake is a no-op (nothing has changed yet).
        self._versions[workspace_id] = {
            e.path: e.version
            for e in await self._sb.walk(handle, "/")
            if not should_ignore(e.path, self._ignores)
        }
        # #366: mark the sandbox authoritative AFTER the restore completes. The
        # marker lives OUTSIDE the workspace (see Sandbox.mark_ready), so it is
        # never walked/tracked/mirrored. Written last so a crash mid-restore
        # leaves it absent → the mirror skips a half-restored dir instead of
        # trusting its (incomplete) file set.
        await self._sb.mark_ready(handle)
        logger.info("sync: sandbox %s marked ready (workspace %s)", handle.id, workspace_id)
        self._record("restore", workspace_id, started, n_files=n, bytes=n_bytes)
        return n

    async def mirror(self, workspace_id: str, handle: SandboxHandle) -> int:
        """PULL the live sandbox into the snapshot: copy files whose `version`
        changed since the last mirror, and delete snapshot files the sandbox no
        longer has. Returns how many paths were written or deleted.

        #366: UPLOADS are always safe (add/update never loses data) and run
        unconditionally. DELETIONS are honoured only when the sandbox is a
        complete, authoritative state — `is_ready` holds BEFORE AND AFTER the
        walk. A teardown drops readiness FIRST (unlinks the out-of-workspace
        marker before rmtree), so a mid-walk reap is caught by the second check;
        a not-yet-restored (empty/partial) sandbox is not ready at all. A
        vanished sandbox (walk raises SandboxNotFound) is a clean skip — nothing
        to mirror, and certainly nothing to delete."""
        started = time.monotonic()
        try:
            ready_before = await self._sb.is_ready(handle)
            entries = await self._sb.walk(handle, "/")
        except SandboxNotFound:
            logger.warning(
                "sync: mirror workspace %s: sandbox %s gone (reaped) -> skip",
                workspace_id,
                handle.id,
            )
            return 0  # sandbox gone (reaped) → skip; the snapshot is the archive
        logger.debug(
            "sync: mirror workspace %s: walk ok, ready_before=%s", workspace_id, ready_before
        )
        prev = self._versions.get(workspace_id, {})
        seen: dict[str, str] = {}
        sizes: dict[str, int] = {}
        n_uploaded = 0
        n_bytes = 0
        for entry in entries:
            if should_ignore(entry.path, self._ignores):
                continue
            seen[entry.path] = entry.version
            sizes[entry.path] = entry.size
            if prev.get(entry.path) == entry.version:
                continue  # unchanged since last mirror
            # Stream sandbox → FileStore through a staging file so a big file
            # the agent produced never sits whole in RAM (issue #219).
            with _staging_file() as tmp:
                await self._sb.download_to_file(handle, entry.path, tmp)
                await self._fs.write_from_path(workspace_id, entry.path, tmp, None)
                n_bytes += tmp.stat().st_size
            n_uploaded += 1
        n_deleted = 0
        if ready_before and await self._ready_after(handle):
            logger.debug(
                "sync: mirror workspace %s: deletions honored (ready sandwich held)", workspace_id
            )
            for path in prev:
                if path not in seen and await self._fs.exists(workspace_id, path):
                    await self._fs.delete(workspace_id, path)
                    n_deleted += 1
        self._versions[workspace_id] = seen
        # Only publish a measurement of a COMPLETE sandbox: a not-yet-ready one is
        # mid-restore, so its file set is partial and would under-report.
        if ready_before and self._on_measured is not None:
            self._on_measured(workspace_id, sizes)
        # #407: n_files = the whole (non-ignored) file count in the workspace —
        # the per-workspace cardinality signal; n_uploaded/n_deleted/bytes are
        # this mirror's actual I/O.
        self._record(
            "mirror",
            workspace_id,
            started,
            n_files=len(seen),
            n_uploaded=n_uploaded,
            n_deleted=n_deleted,
            bytes=n_bytes,
        )
        logger.info(
            "sync: mirror workspace %s: +%d uploaded, -%d deleted, %d bytes",
            workspace_id,
            n_uploaded,
            n_deleted,
            n_bytes,
        )
        return n_uploaded + n_deleted

    async def _ready_after(self, handle: SandboxHandle) -> bool:
        """Gate 2: re-check readiness after the walk. A teardown that began
        during the walk dropped readiness first (or the whole sandbox is gone),
        so a now-not-ready sandbox means 'do not trust this delete'."""
        try:
            return await self._sb.is_ready(handle)
        except SandboxNotFound:
            logger.debug(
                "sync: ready-after: sandbox %s gone -> not ready (skip deletes)", handle.id
            )
            return False
