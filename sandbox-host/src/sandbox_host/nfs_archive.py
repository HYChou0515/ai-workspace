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
set foreign uids on the NFS side. ``restore`` likewise rsyncs files in as root
(no ``-o``); per-uid ownership is re-applied on the LOCAL copy right after, by
the sandbox's ``reown`` (the controller calls it post-restore, pre-mark_ready),
never here (#492 Q3 / #504).

The packed copy (docs/plan-archive-pack.md). Restoring the tree costs one NFS
round trip per path — 88,889 paths took 58 s, at the ingress timeout — so a
reap may also leave ``<root>/<item>.pack.<gen>-<bytes>.tar`` (``pack``, which the
host calls while tearing the sandbox down), a single file the reopen path reads
in one sequential stream. The TREE stays the
truth; the pack is a cache whose validity is its NAME: every write to the tree
first replaces ``<root>/<item>.gen``, so a pack made before that write is named
after a generation that is no longer current and is never chosen. No delete has
to win a race and nothing has to be verified after publishing. Every failure —
a stale name, a half-written tmp, a tar that does not extract — ends as "walk
the tree": slow, never wrong. Every name it writes sits BESIDE the item dir, where a
``--delete`` reconcile of the tree cannot reach them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

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

_GEN_RE = re.compile(r"[0-9a-f]{32}")
# `<item>.pack.<uuid>.tmp`: one per pack being written, so two never share one.
_PACK_TMP_RE = re.compile(r"[0-9a-f]{32}\.tmp")
# `<gen>-<bytes>.tar`: the byte count is part of the promise. GNU tar exits 0 on
# an archive cut at an entry boundary — it extracts the first half and calls it
# the end — so "tar succeeded" does not mean "the workspace is whole".
_PACK_RE = re.compile(r"([0-9a-f]{32})-([0-9]+)\.tar")


class NfsArchive:
    def __init__(
        self,
        nfs_root: Path | str,
        *,
        rsync: str = "rsync",
        tar: str = "tar",
        runner: Runner | None = None,
        pack: bool = True,
    ) -> None:
        self._root = Path(nfs_root)
        self._rsync = rsync
        self._tar = tar
        self._run: Runner = runner or self._default_run
        # `SANDBOX_HOST_ARCHIVE_PACK`: off ⇒ never pack and never read a pack —
        # the restore is exactly the tree walk it was before.
        self._pack = pack

    @property
    def packing(self) -> bool:
        """Whether a pack would be made (``SANDBOX_HOST_ARCHIVE_PACK``) — so the
        host closes a sandbox for one only when it would."""
        return self._pack

    def _item_dir(self, item_id: str) -> Path:
        return self._root / _check_item(item_id)

    async def persist(
        self,
        item_id: str,
        workspace_dir: Path,
        *,
        delete: bool,
    ) -> bool:
        """rsync the sandbox's local working dir → the item's NFS archive. With
        ``delete`` the archive is reconciled to match exactly (turn-end / reap /
        shutdown, at a quiesced ``.ready`` sandbox); without it the copy is
        additive only (the 30 s mid-turn durability checkpoint).

        Every call first moves the item to a new generation, so any pack made
        before it stops being chosen. Returns whether a reconcile RAN — the one
        state after which the tree is a copy of the dir, and so the only one a
        pack may follow."""
        dst = self._item_dir(item_id)
        await asyncio.to_thread(dst.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._new_generation, item_id)
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
        return reconcile

    async def pack(self, item_id: str, workspace_dir: Path) -> bool:
        """tar the local dir into ``<item>.pack.<gen>-<bytes>.tar``, named after
        the generation current BEFORE the tar. Returns whether one was published.

        The caller's job is that nothing writes the DIR meanwhile (the host packs
        only while tearing a sandbox down, with the sandbox closed to requests).
        The generation covers the TREE: a write to it during the tar changes the
        generation first, so the pack is stale the moment it is published — slow
        on the next reopen, never wrong."""
        if not self._pack:
            return False
        gen = await asyncio.to_thread(self._current_generation, item_id)
        if gen is None:
            return False
        tmp = self._root / f"{item_id}.pack.{uuid.uuid4().hex}.tmp"
        rc, err = await self._run(
            [self._tar, "--format=posix", "-C", str(workspace_dir), "-cf", str(tmp), "."]
        )
        try:
            if rc != 0:
                logger.warning(
                    "archive: pack of item %s failed (tar exited %d: %s)",
                    item_id,
                    rc,
                    err.decode(errors="replace").strip(),
                )
                return False
            size = (await asyncio.to_thread(tmp.stat)).st_size
            await asyncio.to_thread(os.replace, tmp, self._pack_path(item_id, gen, size))
            return True
        except FileNotFoundError:
            # A writer's generation change cleared this item's tmps, ours among
            # them: that writer has already made any pack of the old tree stale.
            return False
        finally:
            await asyncio.to_thread(tmp.unlink, missing_ok=True)

    def _new_generation(self, item_id: str) -> str:
        """Move the item to a fresh generation and clear what the old one left.

        Written before the tree is touched: a pack whose name carries the old
        generation must already be stale when the first byte of the new tree
        lands. Replaced atomically (tmp + rename), so a reader sees one value or
        the other. The tmp is named after the generation it carries: nothing
        serialises two persists of one item (a turn-end flush and another pod's
        checkpoint), and a shared tmp name made the second rename find nothing.
        The cleanup is housekeeping, not correctness — the name already makes
        those files unusable."""
        gen = uuid.uuid4().hex
        tmp = self._root / f"{_check_item(item_id)}.gen.{gen}.tmp"
        tmp.write_text(gen)
        os.replace(tmp, self._root / f"{item_id}.gen")
        for path in self._pack_files(item_id):
            path.unlink(missing_ok=True)
        return gen

    def _pack_files(self, item_id: str) -> list[Path]:
        """This item's packs and pack tmps — and nothing else. Item ids are free
        text, so `a`'s files are not told apart by prefix alone: `a.pack.x` is
        another item, and `a.pack.notes.txt` is nobody's pack."""
        prefix = f"{item_id}.pack."
        out = []
        for path in self._root.glob(f"{_glob_escape(prefix)}*"):
            rest = path.name[len(prefix) :]
            if _PACK_TMP_RE.fullmatch(rest) or _PACK_RE.fullmatch(rest):
                out.append(path)
        return out

    def _pack_path(self, item_id: str, gen: str, size: int) -> Path:
        return self._root / f"{item_id}.pack.{gen}-{size}.tar"

    def _whole_pack(self, item_id: str, gen: str) -> Path | None:
        """The current generation's pack, if its size is the size its name
        promises. A short (or long) one is treated as absent."""
        prefix = f"{item_id}.pack."
        # The glob is the generation check: `gen` is 32 hex digits, so it is
        # literal inside the pattern.
        for path in self._root.glob(f"{_glob_escape(prefix)}{gen}-*.tar"):
            match = _PACK_RE.fullmatch(path.name[len(prefix) :])
            if match is None:
                continue
            try:
                if path.stat().st_size == int(match.group(2)):
                    return path
            except FileNotFoundError:
                continue
        return None

    def _current_generation(self, item_id: str) -> str | None:
        try:
            gen = (self._root / f"{item_id}.gen").read_text().strip()
        except FileNotFoundError:
            return None
        return gen if _GEN_RE.fullmatch(gen) else None

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
        target = Path(workspace_dir)
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        if self._pack and await self._restore_from_pack(item_id, target):
            return True
        await self._invoke([self._rsync, _RSYNC_FLAGS, f"{src}/", f"{target}/"])
        return True

    async def _restore_from_pack(self, item_id: str, target: Path) -> bool:
        """Extract the CURRENT generation's pack, if there is one. A pack named
        after any other generation is not looked at. One that fails to extract
        leaves the dir emptied, never half-filled: the caller then walks the
        tree, and a half dir marked ready would be reconciled INTO the archive.
        ``--no-same-owner`` because rsync never sets owners (root_squash) and
        tar run as root would, from the headers; ``reown`` follows either way."""
        gen = await asyncio.to_thread(self._current_generation, item_id)
        if gen is None:
            return False
        pack = await asyncio.to_thread(self._whole_pack, item_id, gen)
        if pack is None:
            return False
        rc, err = await self._run(
            [self._tar, "-C", str(target), "-xf", str(pack), "--no-same-owner"]
        )
        if rc == 0:
            return True
        # Nobody else sees this: the reopen is just slow again. The host sets no
        # log level, so anything quieter than a warning never reaches the pod log.
        logger.warning(
            "archive: pack %s for item %s did not extract (tar exited %d: %s); "
            "walking the tree instead",
            pack.name,
            item_id,
            rc,
            err.decode(errors="replace").strip(),
        )
        await asyncio.to_thread(_empty_dir, target)
        return False

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


def _glob_escape(text: str) -> str:
    """`text` as a literal inside a glob pattern (item ids are free text)."""
    return re.sub(r"([*?\[])", r"[\1]", text)


def _empty_dir(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
