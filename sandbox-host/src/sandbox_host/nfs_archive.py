"""NfsArchive — host-side rsync between a sandbox's local working dir and the
durable NFS archive (#492).

Doing the bulk copy HERE, on the host, is the whole fix: it is a local-disk↔NFS
``rsync`` that never crosses the app↔host network, so it cannot hang the way the
old per-file HTTP mirror did (the app pulling every file over a connection to a
dying host, with no read deadline). The host owns exactly one item's live dir at
a time, so its ``rsync`` reconciles against the REAL directory — not a per-pod
in-memory diff — which is why ``--delete`` is safe here even without sticky
routing (#492 Q8).

Ownership: the archive is written with ``-rlptD`` (perms + times, but NOT owner
/ group), so it survives NFS ``root_squash`` — the host does not, and need not,
set foreign uids on the NFS side. Per-uid ownership is re-applied on the LOCAL
copy at restore time by the sandbox provisioner, never here (#492 Q3).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

# A runner takes the rsync argv and returns (returncode, stderr) — the seam that
# lets tests assert the command without shelling out.
Runner = Callable[[list[str]], Awaitable[tuple[int, bytes]]]


class RsyncError(RuntimeError):
    """An rsync invocation exited non-zero."""


def _check_item(item_id: str) -> str:
    if item_id in ("", ".", "..") or "/" in item_id or "\\" in item_id:
        raise ValueError(f"unsafe item_id: {item_id!r}")
    return item_id


# Recursive, copy symlinks as symlinks, preserve permissions + mtimes, and
# devices/specials — but deliberately NOT owner/group (see module docstring).
_RSYNC_FLAGS = "-rlptD"


class NfsArchive:
    def __init__(
        self,
        nfs_root: Path | str,
        *,
        rsync: str = "rsync",
        runner: Runner | None = None,
    ) -> None:
        self._root = Path(nfs_root)
        self._rsync = rsync
        self._run: Runner = runner or self._default_run

    def _item_dir(self, item_id: str) -> Path:
        return self._root / _check_item(item_id)

    async def persist(self, item_id: str, workspace_dir: Path, *, delete: bool) -> None:
        """rsync the sandbox's local working dir → the item's NFS archive. With
        ``delete`` the archive is reconciled to match exactly (turn-end / reap /
        shutdown, at a quiesced ``.ready`` sandbox); without it the copy is
        additive only (the 30 s mid-turn durability checkpoint)."""
        dst = self._item_dir(item_id)
        await asyncio.to_thread(dst.mkdir, parents=True, exist_ok=True)
        # #492 safety valve: a ``--delete`` from an EMPTY source over a NON-empty
        # archive wipes durable data — the exact disaster this feature exists to
        # prevent. An empty source is indistinguishable here from a silently-failed
        # / half restore (a stale NFS handle, a reaped dir), so REFUSE the
        # destructive reconcile in that case (downgrade to an additive copy, which
        # from an empty source is a no-op) and leave the archive intact. A
        # genuinely-emptied workspace keeping its old archive (zombie files) is a
        # strictly safer, recoverable failure than an irreversible wipe. The host's
        # ``.ready`` gate already blocks the half-restore case; this is
        # defence-in-depth for the residual "rsync restore exited 0 but copied
        # nothing" edge.
        reconcile = delete and not await self._would_wipe(Path(workspace_dir), dst)
        argv = [self._rsync, _RSYNC_FLAGS]
        if reconcile:
            argv.append("--delete")
        # Trailing slashes: copy the CONTENTS of workspace_dir into the item dir.
        argv += [f"{workspace_dir}/", f"{dst}/"]
        await self._invoke(argv)

    async def _would_wipe(self, src: Path, dst: Path) -> bool:
        """True when a ``--delete`` reconcile would WIPE durable data: the source
        dir is empty (a vanished / silently-failed restore) while the archive is
        not. Both probes run off the loop (NFS stat)."""
        return await asyncio.to_thread(self._is_empty, src) and not await asyncio.to_thread(
            self._is_empty, dst
        )

    @staticmethod
    def _is_empty(path: Path) -> bool:
        """True when ``path`` has no entries (or does not exist) — the guard basis
        for refusing a destructive reconcile from a vanished/half-restored dir."""
        try:
            return not any(path.iterdir())
        except FileNotFoundError:
            return True

    async def restore(self, item_id: str, workspace_dir: Path) -> bool:
        """rsync the item's NFS archive → a freshly-created local working dir.
        Returns False (a no-op) when nothing has been archived yet — a brand-new
        item — so the caller knows it's starting cold rather than empty."""
        src = self._item_dir(item_id)
        if not await asyncio.to_thread(src.is_dir):
            return False
        await asyncio.to_thread(Path(workspace_dir).mkdir, parents=True, exist_ok=True)
        await self._invoke([self._rsync, _RSYNC_FLAGS, f"{src}/", f"{workspace_dir}/"])
        return True

    async def _invoke(self, argv: list[str]) -> None:
        rc, stderr = await self._run(argv)
        if rc != 0:
            raise RsyncError(f"rsync exited {rc}: {stderr.decode(errors='replace')}")

    async def _default_run(self, argv: list[str]) -> tuple[int, bytes]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await proc.communicate()
        return proc.returncode or 0, err
