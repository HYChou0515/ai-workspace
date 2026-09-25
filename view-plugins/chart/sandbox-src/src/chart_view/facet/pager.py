"""The pager's answers (plan-view-plugins-pr4 P4): the index a gallery sorts
from, a page of records, and one group's exact values.

Everything is answered in PR 2's wire shapes (``chart_view/wire.py``): a
continuous record is a ``q8`` column, a category record a width-1 ``cat``
column, exact values an ``f64`` column. One decoder and one raster core then
serve both the thumbnail and the full view (Q13).

Standard library only: this runs on every scroll and never pays a pandas
import. A cache it cannot use raises ``CacheUnusable`` for the command to
rebuild; a page asked against an older build raises ``StaleIndex`` for the
gallery to refetch the index.
"""

from __future__ import annotations

import base64
import contextlib
import os
import re
import sys
from array import array
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from chart_view.facet import (
    CacheIndex,
    CacheRebuilt,
    CategoryScale,
    ContinuousScale,
    read_exact,
    read_groups,
    read_index,
)

_DIGEST = re.compile(r"[0-9a-f]{64}")


class StaleIndex(Exception):
    """The cache was rebuilt since the browser read its index: the positions it
    sorted may now name other groups."""


def _path(root: Path, digest: str) -> Path:
    # The digest comes from the browser, in argv, and names a file: it must be
    # exactly what cache_file makes (sha256 hex), never a path.
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError(f"cache digest {digest!r} is not 64 lowercase hex characters")
    return root / f"{digest}.vcache"


def _used(path: Path) -> None:
    """Mark the cache recently used for the cap's LRU (atime is unreliable on NFS)."""
    with contextlib.suppress(OSError):  # removed since the read: the next read reports it
        os.utime(path)


def _current(root: Path, digest: str, build: str) -> tuple[Path, CacheIndex]:
    path = _path(root, digest)
    index = read_index(path)
    if index.build_id.decode("ascii") != build:
        raise StaleIndex(f"cache {digest} was rebuilt; refetch its index")
    return path, index


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def index_payload(root: Path, digest: str) -> dict[str, Any]:
    path = _path(root, digest)
    index = read_index(path)
    _used(path)
    return {
        "build": index.build_id.decode("ascii"),
        "scale": index.scale.to_json(),
        "facet": list(index.facet),
        "zones": dict(index.zones),
        "cells": index.cells,
        "layout": index.layout,
        "columns": index.columns,
        "groups": [{"key": list(g.key), "sort": g.sort} for g in index.groups],
    }


def _column(scale: ContinuousScale | CategoryScale, record: bytes) -> dict[str, Any]:
    if isinstance(scale, ContinuousScale):
        return {"kind": "q8", "min": scale.lo, "max": scale.hi, "codes": _b64(record)}
    return {"kind": "cat", "levels": list(scale.labels), "width": 1, "codes": _b64(record)}


def page_payload(root: Path, digest: str, build: str, positions: Sequence[int]) -> dict[str, Any]:
    path, index = _current(root, digest, build)
    try:
        records = read_groups(path, index, positions)
    except CacheRebuilt:  # a rebuild landed between the build check and this read
        raise StaleIndex(f"cache {digest} was rebuilt; refetch its index") from None
    _used(path)
    return {"build": build, "groups": [_column(index.scale, r) for r in records]}


def exact_payload(root: Path, digest: str, build: str, position: int) -> dict[str, Any]:
    path, index = _current(root, digest, build)
    try:
        if isinstance(index.scale, CategoryScale):
            # a category cache stores codes only, and a code IS the exact value:
            # the enlarged tile reads the label under the pointer from its record
            (record,) = read_groups(path, index, [position])
            _used(path)
            return _column(index.scale, record)
        values = read_exact(path, index, position)
    except CacheRebuilt:  # a rebuild landed between the build check and this read
        raise StaleIndex(f"cache {digest} was rebuilt; refetch its index") from None
    _used(path)
    data = array("d", (float("nan") if v is None else v for v in values))
    if sys.byteorder != "little":  # pragma: no cover - the wire is little-endian
        data.byteswap()
    return {"kind": "f64", "data": _b64(data.tobytes())}
