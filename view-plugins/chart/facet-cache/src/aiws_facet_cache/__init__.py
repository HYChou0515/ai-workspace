"""The facet cache: one file per (source version, spec transform).

Layout, little-endian::

    MAGIC (8 bytes) | header length (u32) | header (UTF-8 JSON) | records

The header is the index: the scale, the cell count, an opaque ``layout`` the
builder uses to place cells, and one entry per group (key + sort values). Every
record is exactly ``cells`` bytes, so group ``i`` starts at
``data_offset + i * cells`` — the offset is derived, never stored, so it cannot
disagree with the data. One byte per cell: a category code, or a continuous
value quantized to 255 levels. ``MISSING`` (255) is a cell the group has no row
for.

A page is a byte slice of the records; reading one needs no pandas
(plan-view-plugins-pr4 P2/P4).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAGIC = b"AIWSVC01"
MISSING = 255
LEVELS = 254  # the top quantized code; codes run 0..LEVELS, MISSING is outside
_LEN = struct.Struct("<I")


class CacheUnusable(Exception):
    """Missing, of another format, or cut short: the caller rebuilds."""


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

    def encode(self, values: Sequence[float | None]) -> bytes:
        span = self.hi - self.lo
        out = bytearray()
        for v in values:
            if v is None or math.isnan(v):  # pandas' missing cell is NaN
                out.append(MISSING)
            elif span <= 0:
                out.append(0)
            else:
                q = round((v - self.lo) / span * LEVELS)
                out.append(min(max(q, 0), LEVELS))
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
        return [None if b == MISSING else self.labels[b] for b in record]

    def to_json(self) -> dict[str, Any]:
        return {"kind": "category", "labels": list(self.labels)}


Scale = ContinuousScale | CategoryScale


@dataclass(frozen=True)
class Group:
    key: str
    sort: Mapping[str, Any]
    values: Sequence[Any]


@dataclass(frozen=True)
class IndexEntry:
    key: str
    sort: dict[str, Any]


@dataclass(frozen=True)
class CacheIndex:
    scale: Scale
    cells: int
    layout: dict[str, Any]
    groups: list[IndexEntry] = field(default_factory=list)
    data_offset: int = 0


def _scale_from_json(raw: Mapping[str, Any]) -> Scale:
    if raw["kind"] == "category":
        return CategoryScale(labels=raw["labels"])
    return ContinuousScale(lo=raw["lo"], hi=raw["hi"])


def write_cache(
    path: Path,
    *,
    scale: Scale,
    cells: int,
    layout: Mapping[str, Any],
    groups: Sequence[Group],
) -> None:
    """Write the whole cache or nothing: a failed or interrupted build leaves no
    file at ``path`` for a reader to mistake for a cache."""
    records = []
    for g in groups:
        if len(g.values) != cells:
            raise ValueError(f"group {g.key!r} has {len(g.values)} cells, the cache has {cells}")
        records.append(scale.encode(g.values))
    header = json.dumps(
        {
            "scale": scale.to_json(),
            "cells": cells,
            "layout": dict(layout),
            "groups": [{"key": g.key, "sort": dict(g.sort)} for g in groups],
        },
        separators=(",", ":"),
    ).encode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(MAGIC)
            f.write(_LEN.pack(len(header)))
            f.write(header)
            for record in records:
                f.write(record)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_index(path: Path) -> CacheIndex:
    """Read the header only. Raises ``CacheUnusable`` for anything short of a
    complete cache in this format — including a last record cut short, so a
    page read of a cache whose index was accepted never comes back short."""
    try:
        with path.open("rb") as f:
            if f.read(len(MAGIC)) != MAGIC:
                raise CacheUnusable(f"{path}: not a facet cache in this format")
            head = f.read(_LEN.size)
            if len(head) != _LEN.size:
                raise CacheUnusable(f"{path}: cut short in its header")
            (n,) = _LEN.unpack(head)
            raw = json.loads(f.read(n))
            size = os.fstat(f.fileno()).st_size
        index = CacheIndex(
            scale=_scale_from_json(raw["scale"]),
            cells=raw["cells"],
            layout=raw["layout"],
            groups=[IndexEntry(key=g["key"], sort=g["sort"]) for g in raw["groups"]],
            data_offset=len(MAGIC) + _LEN.size + n,
        )
    except FileNotFoundError:
        raise CacheUnusable(f"{path}: no cache") from None
    except (ValueError, KeyError, TypeError) as e:  # JSONDecodeError is a ValueError
        raise CacheUnusable(f"{path}: unreadable header ({e})") from None
    if size != index.data_offset + len(index.groups) * index.cells:
        raise CacheUnusable(f"{path}: {size} bytes, not the size its header describes")
    return index


def read_records(path: Path, index: CacheIndex, start: int, stop: int) -> list[bytes]:
    """Groups ``[start, stop)`` in written order; ``stop`` past the end is clamped."""
    stop = min(stop, len(index.groups))
    if start >= stop:
        return []
    want = (stop - start) * index.cells
    try:
        with path.open("rb") as f:
            f.seek(index.data_offset + start * index.cells)
            blob = f.read(want)
    except FileNotFoundError:
        raise CacheUnusable(f"{path}: removed after its index was read") from None
    if len(blob) != want:
        raise CacheUnusable(f"{path}: changed after its index was read")
    return [blob[i : i + index.cells] for i in range(0, want, index.cells)]
