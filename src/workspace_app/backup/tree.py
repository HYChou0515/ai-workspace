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

The residual limitation is rsync's too: a file whose content changes while size
AND mtime stay identical is not detected. `--checksum` is the only cure and it
costs a full read of the tree every night.
"""

from __future__ import annotations

import logging
import os
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: `{arcname: (size, mtime_ns)}` for every regular file and symlink in the tree.
Manifest = dict[str, tuple[int, int]]


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

    with tarfile.open(artifact, "w") as tar:
        for path, arcname in _walk(root, skipped):
            try:
                stat = path.lstat()
            except OSError as exc:
                skipped.append(f"{arcname}: {type(exc).__name__}")
                continue

            if _is_special(stat):
                # A FIFO, socket or device has no content to archive, and
                # `extractall(filter="data")` refuses it on the way back in. Not
                # carrying it is the honest answer; saying so is the rest of it.
                skipped.append(f"{arcname}: not a regular file or symlink")
                continue

            manifest[arcname] = (stat.st_size, stat.st_mtime_ns)
            if stat.st_mtime_ns > cutoff_ns:
                continue  # belongs to the next run's window
            if previous is not None and previous.get(arcname) == manifest[arcname]:
                continue  # unchanged since the previous run

            if _before_add is not None:
                _before_add(path)
            try:
                tar.add(path, arcname=arcname, recursive=False)
            except (OSError, tarfile.TarError) as exc:
                # The tree is live and has no snapshot: between the walk and the
                # read an agent can delete or truncate a file. One user's `rm`
                # must not fail the night's backup for everybody else.
                skipped.append(f"{arcname}: {type(exc).__name__}")
                continue
            carried.append(arcname)

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

    Directories are yielded before their contents and ALWAYS carried: a tar that
    holds a file without its parent restores into nothing. A directory that
    cannot be listed is counted rather than raised — the same rule as a file.
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

    with tarfile.open(artifact, "r") as tar:
        for member in tar:
            reason = _refuse(member, base, root)
            if reason is not None:
                skipped.append(f"{member.name}: {reason}")
                continue
            try:
                tar.extract(member, root, set_attrs=True, filter="tar")
            except (OSError, tarfile.TarError) as exc:
                skipped.append(f"{member.name}: {type(exc).__name__}")
                continue
            restored += 1

    if skipped:
        logger.warning(
            "restore: %d member(s) of %s were not written: %s",
            len(skipped),
            artifact,
            ", ".join(skipped[:10]),
        )
    return ExtractResult(restored=restored, skipped=tuple(skipped))


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
        resolved = (target.parent / link).resolve()
        if resolved != base and base not in resolved.parents:
            return "link escapes the workspace tree"
    return None


def utc_now() -> datetime:
    return datetime.now(UTC)
