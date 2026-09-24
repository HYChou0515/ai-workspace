"""The cache cap (plan-view-plugins-pr4 P3). The views cache lives in the
sandbox's infra area, outside the workspace quota (Q12), so each build bounds
it: least recently used first, by the mtime every read sets (``pager._used``;
atime is unreliable on NFS)."""

from __future__ import annotations

import time
from pathlib import Path

from aiws_facet_cache import TMP_SUFFIX

CACHE_SUFFIX = ".vcache"


def _entries(root: Path) -> list[tuple[Path, int, float]]:
    """(path, size, mtime) of every cache and temp file under ``root``."""
    out = []
    try:
        children = list(root.iterdir())
    except FileNotFoundError:
        return []
    for p in children:
        if not (p.name.endswith(CACHE_SUFFIX) or p.name.endswith(TMP_SUFFIX)):
            continue
        try:
            st = p.stat()
        except FileNotFoundError:  # removed by another build since the listing
            continue
        out.append((p, st.st_size, st.st_mtime))
    return out


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

    - ``keep`` (the cache being opened) is never removed, even if it alone is
      over the cap: the view that asked for it must still open.
    - A temp file older than ``tmp_grace_s`` is a build that died (a SIGKILL
      skips its cleanup) and is always removed. A younger one is a build in
      progress: it counts toward the total but is left, since removing it
      would fail that build's ``os.replace``.
    - Nothing else in ``root`` is touched or counted.
    """
    now = time.time() if now is None else now
    removed: list[Path] = []
    total = 0
    candidates = []
    for path, size, mtime in _entries(root):
        if path.name.endswith(TMP_SUFFIX):
            if now - mtime > tmp_grace_s:
                path.unlink(missing_ok=True)
                removed.append(path)
            else:
                total += size
            continue
        total += size
        if path != keep:
            candidates.append((mtime, path, size))
    for _, path, size in sorted(candidates):
        if total <= cap_bytes:
            break
        path.unlink(missing_ok=True)
        removed.append(path)
        total -= size
    return removed
