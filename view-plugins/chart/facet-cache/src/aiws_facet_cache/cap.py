"""The cache cap (plan-view-plugins-pr4 P3). The views cache lives in the
sandbox's infra area, outside the workspace quota (Q12), so each build bounds
it: least recently used first, by the mtime every read sets (``pager._used``;
atime is unreliable on NFS)."""

from __future__ import annotations

import os
import stat
import tempfile
import time
from pathlib import Path

from aiws_facet_cache import TMP_SUFFIX

CACHE_SUFFIX = ".vcache"


def _entries(root: Path) -> list[tuple[Path, int, float]]:
    """(path, size, mtime) of every regular cache or temp file directly in
    ``root``. A directory, a symlink (dangling or not) or anything else is not
    ours to count or remove; an entry gone since the listing is skipped."""
    try:
        children = list(root.iterdir())
    except OSError:  # missing, not a directory, unreadable: nothing to bound
        return []
    out = []
    for p in children:
        if not (p.name.endswith(CACHE_SUFFIX) or p.name.endswith(TMP_SUFFIX)):
            continue
        try:
            st = p.lstat()
        except OSError:  # gone since the listing (another build), or unreadable
            continue
        if stat.S_ISREG(st.st_mode):
            out.append((p, st.st_size, st.st_mtime))
    return out


def _fs_now(root: Path) -> float:
    """The file system's own clock: the mtime it stamps on a file made now. On
    NFS that is the server's clock, the one every temp file was stamped by, so
    a pod clock that is off cannot age a live build's temp file."""
    try:
        fd, name = tempfile.mkstemp(dir=root, prefix=".clock.", suffix=".probe")
    except OSError:  # no such dir, or not writable: nothing of ours is in it to age
        return time.time()
    try:
        return os.fstat(fd).st_mtime
    finally:
        os.close(fd)
        Path(name).unlink(missing_ok=True)


def _remove(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # not ours to remove after all (permissions): leave it
        return False
    return True


def enforce_cap(
    root: Path,
    *,
    cap_bytes: int,
    keep: Path | None,
    now: float | None = None,
    tmp_grace_s: float = 3600.0,
) -> list[Path]:
    """Remove caches, least recently used first, until what is left fits in
    ``cap_bytes``; return what was removed.

    - ``keep`` (the cache being opened, a file in ``root``) is never removed,
      even if it alone is over the cap: the view that asked must still open.
      It is matched by name, so any spelling of its path protects it.
    - "Now" is the file system's own clock by default (``_fs_now``), the one
      that stamped every temp file, so a pod clock that is off does not age
      them. A temp file stamped more than ``tmp_grace_s`` from that now, either
      way, is treated as dead and removed: it is older than any build takes (a
      SIGKILL skipped its cleanup), or stamped further ahead than a live build
      could be. The cap cannot see a build's process, so a single write that
      stalls past ``tmp_grace_s`` would be swept and fail its ``os.replace``
      loudly. A nearer temp file counts toward the total but is left.
    - Nothing but regular cache and temp files in ``root`` is touched or
      counted, and a file that cannot be removed is skipped.
    """
    now = _fs_now(root) if now is None else now
    keep_name = keep.name if keep is not None else None
    removed: list[Path] = []
    total = 0
    candidates = []
    for path, size, mtime in _entries(root):
        if path.name.endswith(TMP_SUFFIX):
            if abs(now - mtime) > tmp_grace_s:
                if _remove(path):
                    removed.append(path)
            else:
                total += size
            continue
        total += size
        if path.name != keep_name:
            candidates.append((mtime, path, size))
    for _, path, size in sorted(candidates):
        if total <= cap_bytes:
            break
        if _remove(path):
            removed.append(path)
            total -= size
    return removed
