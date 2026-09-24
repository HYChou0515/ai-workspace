"""The facet cache: one file per (source version, spec transform).

Layout, little-endian::

    MAGIC (8 bytes) | header length (u32) | header (UTF-8 JSON)
    | records: one per group, ``cells`` bytes each
    | exact values (continuous scales only): one per group, ``cells`` float64 each

The header is the index: the scale, the facet columns, the cell count, an
opaque ``layout`` the builder uses to place cells, and one entry per group (its
key, one value per facet column, and its sort values). Group ``i``'s record
starts at ``data_offset + i * cells`` and its exact values at
``exact_offset + i * cells * 8`` — offsets are derived, never stored, so they
cannot disagree with the data.

A record is one byte per cell: a category code, or a continuous value quantized
to 255 levels (codes 0..254). ``MISSING`` (255) is a cell the group has no row
for. The quantized record colours a thumbnail; the exact values back an
enlarged group's tooltips, so neither needs the source re-read.

The header is strict JSON (no NaN/Infinity literals), because the gallery
parses it in the browser. Reading needs only the standard library, so the
pager never pays a pandas import (plan-view-plugins-pr4 P2/P4).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import struct
import sys
import tempfile
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

MAGIC = b"AIWSVC01"
MISSING = 255
LEVELS = 254  # the top quantized code; codes run 0..LEVELS, MISSING is outside
TMP_SUFFIX = ".tmp"  # a build in progress, or one a SIGKILL left behind
_LEN = struct.Struct("<I")
_EXACT = 8  # bytes per exact value (float64)


class CacheUnusable(Exception):
    """Missing, unreadable, of another format, cut short, or replaced since its
    index was read: the caller rebuilds."""


def transform_hash(transform: Mapping[str, Any]) -> str:
    """A stable digest of the part of a spec that shapes the cache: key order is
    irrelevant, list order is not (``groupby: [x, y]`` is not ``[y, x]``)."""
    canonical = json.dumps(transform, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class CacheKey:
    """What a cache was built from. ``size`` and ``mtime_ns`` stand in for the
    source's version, so an edited source is a new file, never a stale hit."""

    source_path: str
    size: int
    mtime_ns: int
    transform_hash: str

    def digest(self) -> str:
        return transform_hash(
            {
                "source_path": self.source_path,
                "size": self.size,
                "mtime_ns": self.mtime_ns,
                "transform_hash": self.transform_hash,
            }
        )


def cache_file(root: Path, key: CacheKey) -> Path:
    return root / f"{key.digest()}.vcache"


@dataclass(frozen=True)
class ContinuousScale:
    lo: float
    hi: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.lo) and math.isfinite(self.hi) and self.lo <= self.hi):
            raise ValueError(f"scale range {self.lo}..{self.hi} is not finite and ordered")

    def encode(self, values: Sequence[float | None]) -> bytes:
        span = self.hi - self.lo
        out = bytearray()
        for v in values:
            if v is None or math.isnan(v):  # pandas' missing cell is NaN
                out.append(MISSING)
            elif span <= 0 or v <= self.lo:
                out.append(0)
            elif v >= self.hi:  # also +inf, which round() cannot take
                out.append(LEVELS)
            else:
                out.append(round((v - self.lo) / span * LEVELS))
        return bytes(out)

    def decode(self, record: bytes) -> list[float | None]:
        step = (self.hi - self.lo) / LEVELS
        return [
            None if b == MISSING else self.hi if b == LEVELS else self.lo + b * step for b in record
        ]

    def to_json(self) -> dict[str, Any]:
        return {"kind": "continuous", "lo": self.lo, "hi": self.hi}


@dataclass(frozen=True)
class CategoryScale:
    labels: Sequence[str]

    def __post_init__(self) -> None:
        if len(self.labels) > LEVELS + 1:
            raise ValueError(
                f"{len(self.labels)} categories; one byte per cell holds at most {LEVELS + 1}"
            )

    def encode(self, values: Sequence[str | None]) -> bytes:
        code = {label: i for i, label in enumerate(self.labels)}
        try:
            return bytes(MISSING if v is None else code[v] for v in values)
        except KeyError as e:
            raise ValueError(f"value {e.args[0]!r} is not one of the scale's categories") from None

    def decode(self, record: bytes) -> list[str | None]:
        try:
            return [None if b == MISSING else self.labels[b] for b in record]
        except IndexError:
            raise CacheUnusable("a cell's category code is outside the labels") from None

    def to_json(self) -> dict[str, Any]:
        return {"kind": "category", "labels": list(self.labels)}


Scale = ContinuousScale | CategoryScale


@dataclass(frozen=True)
class Group:
    key: tuple[str, ...]  # one value per facet column, in the facet's order
    sort: Mapping[str, Any]
    values: Sequence[Any]


@dataclass(frozen=True)
class IndexEntry:
    key: tuple[str, ...]
    sort: dict[str, Any]


@dataclass(frozen=True)
class CacheIndex:
    scale: Scale
    facet: tuple[str, ...]
    cells: int
    layout: dict[str, Any]
    groups: list[IndexEntry]
    data_offset: int
    # Which file this index describes: a rebuild swaps in a new file (os.replace
    # gives it a new inode), and a page read through a stale index must fail
    # rather than slice the new file at the old offsets.
    identity: tuple[int, int, int]

    @property
    def exact_offset(self) -> int:
        return self.data_offset + len(self.groups) * self.cells

    @property
    def size(self) -> int:
        exact = len(self.groups) * self.cells * _EXACT
        return self.exact_offset + (exact if isinstance(self.scale, ContinuousScale) else 0)


def _identity(st: os.stat_result) -> tuple[int, int, int]:
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _sort_value(field: str, v: Any) -> Any:
    """A sort value as strict JSON: NaN (pandas' missing) is null, a numpy
    scalar is its Python value, a date is ISO text (which sorts correctly)."""
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    raise ValueError(f"sort value {field!r} is a {type(v).__name__}, which JSON cannot carry")


def _scale_from_json(raw: Mapping[str, Any]) -> Scale:
    if raw["kind"] == "category":
        return CategoryScale(labels=raw["labels"])
    return ContinuousScale(lo=raw["lo"], hi=raw["hi"])


def write_cache(
    path: Path,
    *,
    scale: Scale,
    facet: Sequence[str],
    cells: int,
    layout: Mapping[str, Any],
    groups: Sequence[Group],
) -> None:
    """Write the whole cache or nothing. The file appears at ``path`` only when
    complete (a temp file in the same dir, then ``os.replace``); a failed write
    removes its temp file, and one a SIGKILL leaves ends in ``TMP_SUFFIX`` for
    the cache cap to sweep."""
    if cells < 1:
        raise ValueError(f"a group needs at least one cell, not cells={cells}")
    records = []
    exact = array("d")
    for g in groups:
        if len(g.key) != len(facet):
            raise ValueError(
                f"group {g.key!r} has {len(g.key)} key values, the facet is {tuple(facet)!r}"
            )
        if len(g.values) != cells:
            raise ValueError(f"group {g.key!r} has {len(g.values)} cells, the cache has {cells}")
        records.append(scale.encode(g.values))
        if isinstance(scale, ContinuousScale):
            exact.extend(math.nan if v is None else float(v) for v in g.values)
    header = json.dumps(
        {
            "scale": scale.to_json(),
            "facet": list(facet),
            "cells": cells,
            "layout": dict(layout),
            "groups": [
                {"key": list(g.key), "sort": {k: _sort_value(k, v) for k, v in g.sort.items()}}
                for g in groups
            ],
        },
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if exact.itemsize != _EXACT:  # pragma: no cover - every CPython platform
        raise RuntimeError("array('d') is not 8 bytes here")
    if sys.byteorder != "little":  # pragma: no cover - the format is little-endian
        exact.byteswap()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=TMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(MAGIC)
            f.write(_LEN.pack(len(header)))
            f.write(header)
            for record in records:
                f.write(record)
            f.write(exact.tobytes())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _parse_index(raw: Any, data_offset: int, st: os.stat_result) -> CacheIndex:
    # Only we write this header, so a malformed one is corruption, and a missing
    # key or wrong type surfaces as the KeyError/TypeError read_index turns into
    # CacheUnusable. ``cells`` is checked because every offset is computed from it.
    cells = raw["cells"]
    if not (isinstance(cells, int) and not isinstance(cells, bool) and cells >= 1):
        raise TypeError(f"cells is {cells!r}")
    return CacheIndex(
        scale=_scale_from_json(raw["scale"]),
        facet=tuple(raw["facet"]),
        cells=cells,
        layout=raw["layout"],
        groups=[IndexEntry(key=tuple(g["key"]), sort=g["sort"]) for g in raw["groups"]],
        data_offset=data_offset,
        identity=_identity(st),
    )


def read_index(path: Path) -> CacheIndex:
    """Read the header only. Raises ``CacheUnusable`` for anything short of a
    complete cache in this format, including a last record cut short — a
    gallery sorts from the index before it reads any page."""
    try:
        with path.open("rb") as f:
            if f.read(len(MAGIC)) != MAGIC:
                raise CacheUnusable(f"{path}: not a facet cache in this format")
            head = f.read(_LEN.size)
            if len(head) != _LEN.size:
                raise CacheUnusable(f"{path}: cut short in its header")
            (n,) = _LEN.unpack(head)
            raw = json.loads(f.read(n))
            index = _parse_index(raw, len(MAGIC) + _LEN.size + n, os.fstat(f.fileno()))
    except OSError as e:  # missing, a directory, unreadable
        raise CacheUnusable(f"{path}: {e.strerror or e}") from None
    except (ValueError, KeyError, TypeError) as e:  # JSONDecodeError is a ValueError
        raise CacheUnusable(f"{path}: unreadable header ({e})") from None
    if index.identity[1] != index.size:
        raise CacheUnusable(f"{path}: {index.identity[1]} bytes, not the {index.size} expected")
    return index


def _open_same(path: Path, index: CacheIndex) -> BinaryIO:
    try:
        f = path.open("rb")
    except OSError as e:
        raise CacheUnusable(f"{path}: {e.strerror or e}") from None
    if _identity(os.fstat(f.fileno())) != index.identity:
        f.close()
        raise CacheUnusable(f"{path}: replaced after its index was read")
    return f


def _check_positions(index: CacheIndex, positions: Sequence[int]) -> None:
    n = len(index.groups)
    for p in positions:
        if not 0 <= p < n:
            raise IndexError(f"group position {p} is outside 0..{n - 1}")


def read_groups(path: Path, index: CacheIndex, positions: Sequence[int]) -> list[bytes]:
    """The records at ``positions`` (written order), in the order asked — a
    sorted page is scattered positions, read without a rebuild."""
    _check_positions(index, positions)
    with _open_same(path, index) as f:
        out = []
        for p in positions:
            f.seek(index.data_offset + p * index.cells)
            out.append(f.read(index.cells))
    return out


def read_records(path: Path, index: CacheIndex, start: int, stop: int) -> list[bytes]:
    """Groups ``[start, stop)`` in written order; ``stop`` past the end is clamped."""
    # A negative start is refused by read_groups as a position outside the index.
    return read_groups(path, index, range(start, min(stop, len(index.groups))))


def read_exact(path: Path, index: CacheIndex, position: int) -> list[float | None]:
    """One group's exact values, for an enlarged view's tooltips."""
    if not isinstance(index.scale, ContinuousScale):
        raise TypeError("a category cache stores codes only; there are no exact values")
    _check_positions(index, [position])
    with _open_same(path, index) as f:
        f.seek(index.exact_offset + position * index.cells * _EXACT)
        values = array("d", f.read(index.cells * _EXACT))
    if sys.byteorder != "little":  # pragma: no cover
        values.byteswap()
    return [None if math.isnan(v) else v for v in values]
