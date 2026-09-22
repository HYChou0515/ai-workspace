"""Archiving and restoring the sandbox's workspace tree.

The tree is an ordinary file tree, but it is an ordinary file tree that USERS
fill. The sandbox host rsyncs each workspace into it with `-rlptD`
(`sandbox-host/src/sandbox_host/nfs_archive.py:43`), which preserves mtimes and
carries symlinks and specials through verbatim. So this walks a directory
holding whatever somebody's agent left behind, with whatever timestamps came
with it.

One rule follows from that, and it is the reason this module exists rather than
two calls to `tarfile`:

    **A backup must never fail, and must never silently skip, because of what a
    user legitimately put in their own workspace.**

Anything that cannot be carried is counted and named in the receipt. Nothing is
dropped without saying so, and nothing raises.

**Change detection is a manifest diff, not an mtime window.** An mtime filter
looked obvious and is wrong: `unzip`, `tar x`, `cp -p` and a restore all stamp a
brand-new file with an OLD mtime, so an incremental bounded below by the previous
window drops it — and no later run goes back for it, because every later window
starts later still. The file ends up in no archive at all. Comparing against the
previous run's `{path: (size, mtime)}` catches it as what it is: a path that was
not there before. The upper bound stays, because a run's window genuinely ends
at `window_end` and later writes belong to the next run.

Two residual limitations, both rsync's too:

* A file whose content changes while size, mtime AND ctime all stay identical is
  not detected — which in practice means a `cp -p`-style copy that restores an
  older file's timestamps over a same-sized one. `--checksum` is the only cure
  and it costs a full read of the tree every night.
* **Deletions are not propagated.** The manifest knows (a path present before and
  absent now) and the tar format has nowhere to say it, so replaying a chain
  resurrects anything deleted after the full. `docs/deployment.md §16` states
  this where an operator will meet it.

What is NOT a limitation, because it is handled: a file rewritten in the same
clock granule the walk stat'd it in. That one is archived and then deliberately
left out of the manifest, so the next run carries it again.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: `{arcname: (size, mtime_ns, ctime_ns)}` — and read the next sentence before
#: changing it. **A manifest records what the CHAIN HOLDS, not what the tree looks
#: like.** A path the run did not archive — excluded by the window, or skipped
#: because it could not be read — must NOT appear with its on-disk value, or the
#: next run diffs against it, sees "unchanged", and the file ends up in no archive
#: ever. That is the round-1 defect in a new shape, and it is why the entry for
#: such a path is carried forward from the previous manifest instead.
#:
#: `ctime_ns` is in the tuple because mtime granularity belongs to the
#: filesystem, and NFS inherits the server's. On a whole-second filesystem
#: `(size, mtime)` cannot tell a status file rewritten in the same second from
#: one that never changed. ctime moves on any write and cannot be backdated by
#: `utime`, so it closes that. It also moves on `chmod`/`chown`, which re-carries
#: a file whose content did not change — the safe direction for a backup.
Manifest = dict[str, tuple[int, int, int]]


#: How close to "now" an mtime has to be before `(size, mtime, ctime)` stops
#: separating "unchanged" from "rewritten since we looked". Two seconds because
#: that is the coarsest granularity in common use (FAT), and because being
#: generous here costs one extra copy of one file while being stingy costs the
#: file. Measured on this machine: /tmp reports whole-second mtime AND ctime.
_STAMP_SLACK_NS = 2 * 1_000_000_000


@dataclass(frozen=True)
class TarResult:
    """What one tree archive holds, and what it could not take.

    `manifest` describes the tree as it was walked — every path, carried or not —
    so the next run diffs against reality rather than against "what fitted".
    `skipped` names what was left out, because a count with no names is not
    something an operator can act on.
    """

    archive: str
    paths: tuple[str, ...] = ()
    manifest: Manifest = field(default_factory=dict)
    skipped: tuple[str, ...] = ()

    @property
    def files(self) -> int:
        return len(self.paths)


@dataclass(frozen=True)
class ExtractResult:
    restored: int = 0
    skipped: tuple[str, ...] = ()


def tar_tree(
    root: Path,
    artifact: Path,
    *,
    previous: Manifest | None,
    window_end: datetime,
    stamp_slack_ns: int = _STAMP_SLACK_NS,
    _before_add: Callable[[Path], None] | None = None,
) -> TarResult:
    """Archive what changed under `root` since `previous`, up to `window_end`.

    `previous is None` means a full run: everything up to the window end.

    `_before_add` is a test seam for the live-tree race — it runs between listing
    a path and adding it, which is the window in which an agent's `rm` lands. It
    is not called in production and there is no way to configure it.
    """
    manifest: Manifest = {}
    carried: list[str] = []
    skipped: list[str] = []
    cutoff_ns = int(window_end.timestamp() * 1_000_000_000)
    walked_ns = int(datetime.now(UTC).timestamp() * 1_000_000_000)
    held = previous or {}

    def _not_ours(arcname: str) -> None:
        """This run does not hold `arcname`. Say what the CHAIN holds, if anything.

        Carrying the previous entry forward (rather than recording what is on
        disk now) is the whole invariant: an entry means "an archive in this
        chain has these bytes". Recording the on-disk value for a path we did
        not archive makes the next run read it as unchanged, and the file is
        then in no archive at all.
        """
        if arcname in held:
            manifest[arcname] = held[arcname]

    with tarfile.open(artifact, "w") as tar:
        for path, arcname in _walk(root, skipped):
            try:
                stat = path.lstat()
            except OSError as exc:
                skipped.append(f"{arcname}: {type(exc).__name__}")
                _not_ours(arcname)
                continue

            if _is_special(stat):
                # A FIFO, socket or device has no content to archive, and
                # `extractall(filter="data")` refuses it on the way back in. Not
                # carrying it is the honest answer; saying so is the rest of it.
                skipped.append(f"{arcname}: not a regular file or symlink")
                continue

            current = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if stat.st_mtime_ns > cutoff_ns:
                _not_ours(arcname)  # belongs to the next run's window
                continue
            if previous is not None and held.get(arcname) == current:
                manifest[arcname] = current  # unchanged, and the chain has it
                continue
            if walked_ns - max(stat.st_mtime_ns, stat.st_ctime_ns) < stamp_slack_ns:
                # Archived, but NOT recorded as held. The tree is live and the
                # clock is coarse: a file rewritten in the same granule we
                # stat'd it in keeps the same (size, mtime, ctime), so next run
                # would read it as unchanged and never carry the new bytes.
                # Leaving it out of the manifest costs one re-carry and closes
                # that. Measured on this machine: /tmp reports whole-second
                # mtime AND ctime, so the triple alone does not separate them.
                _before_add(path) if _before_add is not None else None
                try:
                    tar.add(path, arcname=arcname, recursive=False)
                except (OSError, tarfile.TarError) as exc:
                    skipped.append(f"{arcname}: {type(exc).__name__}")
                    _not_ours(arcname)
                    continue
                carried.append(arcname)
                _not_ours(arcname)
                continue

            if _before_add is not None:
                _before_add(path)
            try:
                tar.add(path, arcname=arcname, recursive=False)
            except (OSError, tarfile.TarError) as exc:
                # The tree is live and has no snapshot: between the walk and the
                # read an agent can delete or truncate a file. One user's `rm`
                # must not fail the night's backup for everybody else — and the
                # next run must try again, so this claims nothing.
                skipped.append(f"{arcname}: {type(exc).__name__}")
                _not_ours(arcname)
                continue
            carried.append(arcname)
            manifest[arcname] = current

    if skipped:
        logger.warning(
            "backup: %d entr(ies) under %s were not archived: %s",
            len(skipped),
            root,
            ", ".join(skipped[:10]),
        )
    return TarResult(
        archive=str(artifact),
        paths=tuple(carried),
        manifest=manifest,
        skipped=tuple(skipped),
    )


def _walk(root: Path, skipped: list[str]) -> list[tuple[Path, str]]:
    """Every directory and leaf under `root`, as `(path, arcname)`.

    Directories are yielded before their contents so a tar is written parent-first.
    They are NOT always carried — an unchanged directory is skipped like any
    other unchanged entry, and `tarfile` creates a missing parent with default
    permissions on extract. The cost is that such a directory's mode and mtime
    are whatever the extract chose; the alternative (carrying every directory
    every run) is a cost on every file in the tree.

    A directory that cannot be listed is counted rather than raised — the same
    rule as a file.
    """
    out: list[tuple[Path, str]] = [(root, ".")]
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: skipped.append(str(e))):
        here = Path(dirpath)
        for name in sorted(dirnames):
            child = here / name
            out.append((child, "./" + str(child.relative_to(root)).replace(os.sep, "/")))
        for name in sorted(filenames):
            child = here / name
            out.append((child, "./" + str(child.relative_to(root)).replace(os.sep, "/")))
    return out


def _is_special(stat: os.stat_result) -> bool:
    import stat as stat_module

    mode = stat.st_mode
    return not (stat_module.S_ISREG(mode) or stat_module.S_ISDIR(mode) or stat_module.S_ISLNK(mode))


def extract_tree(artifact: Path, root: Path) -> ExtractResult:
    """Unpack a workspace tar under `root`, skipping what it must not write.

    Deliberately NOT `extractall(..., filter="data")`. That filter is right about
    what is unsafe and wrong about what to do: it RAISES, part-way through, on an
    absolute symlink or a special file — inputs this system's own backup
    produces from an ordinary `ln -s /etc/passwd`. One such link in one workspace
    would otherwise take down every restore from then on, with the tree half
    written and no way forward short of hand-editing the tar.

    So each member is checked and either written or counted. The checks are the
    ones `data` makes: nothing may resolve outside `root`, no absolute link
    target, no device or FIFO.
    """
    root.mkdir(parents=True, exist_ok=True)
    base = root.resolve()
    restored = 0
    skipped: list[str] = []
    directories: list[tarfile.TarInfo] = []

    with tarfile.open(artifact, "r") as tar:
        for member in tar:
            reason = _refuse(member, base, root)
            if reason is not None:
                skipped.append(f"{member.name}: {reason}")
                continue
            target = root / member.name
            _clear_conflicting_type(target, member, skipped)
            try:
                # `set_attrs` is withheld for directories and applied in a second
                # pass below — the same thing `extractall` does, and for a reason
                # that bites here: a directory restored with mode 555 cannot then
                # have its own children written into it, so one read-only folder
                # in one workspace takes its whole subtree with it.
                tar.extract(member, root, set_attrs=not member.isdir(), filter="tar")
            except (OSError, tarfile.TarError) as exc:
                skipped.append(f"{member.name}: {type(exc).__name__}")
                continue
            if member.isdir():
                directories.append(member)
            restored += 1

        # Deepest last: a parent's mode must not stop a child's being set.
        for member in sorted(directories, key=lambda m: m.name, reverse=True):
            with contextlib.suppress(OSError):
                tar.chown(member, str(root / member.name), numeric_owner=False)
                tar.chmod(member, str(root / member.name))
                tar.utime(member, str(root / member.name))

    if skipped:
        logger.warning(
            "restore: %d member(s) of %s were not written: %s",
            len(skipped),
            artifact,
            ", ".join(skipped[:10]),
        )
    return ExtractResult(restored=restored, skipped=tuple(skipped))


def _clear_conflicting_type(target: Path, member: tarfile.TarInfo, skipped: list[str]) -> None:
    """Remove what is already at `target` when it is the wrong KIND of thing.

    A chain is replayed over itself, so an earlier run may have put a file where
    this run has a directory (a workspace where `thing` became `thing/`) or the
    reverse. `tarfile` raises `FileExistsError` / `NotADirectoryError` on that
    and, under the skip-don't-raise policy, the whole subtree below it would
    vanish quietly. Same bytes either way; only the type has to give.
    """
    try:
        if not target.exists() and not target.is_symlink():
            return
        wants_dir = member.isdir()
        is_dir = target.is_dir() and not target.is_symlink()
        if wants_dir == is_dir:
            return
        if is_dir:
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:  # pragma: no cover - a target we cannot even inspect
        skipped.append(f"{member.name}: {type(exc).__name__} clearing the old entry")


def _refuse(member: tarfile.TarInfo, base: Path, root: Path) -> str | None:
    """Why this member must not be written, or None to write it."""
    if member.isdev() or member.isfifo():
        return "special file"
    target = (root / member.name).resolve()
    if target != base and base not in target.parents:
        return "resolves outside the workspace tree"
    if member.issym() or member.islnk():
        link = member.linkname
        if os.path.isabs(link):
            return "link to an absolute path"
        # A symlink's target is relative to the link's OWN directory; a HARDLINK's
        # is relative to the archive root (`tarfile` joins it with the extraction
        # path, not the member's parent). Resolving both the same way let
        # `../etc/passwd` through on a hardlink — and `filter="tar"` checks no
        # link targets at all, so this is the only check there is.
        anchor = base if member.islnk() else target.parent
        resolved = (anchor / link).resolve()
        if resolved != base and base not in resolved.parents:
            return "link escapes the workspace tree"
    return None


def utc_now() -> datetime:
    return datetime.now(UTC)
