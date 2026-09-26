"""The facet cache: one file per (source version, spec transform).

Layout, little-endian::

    MAGIC (8 bytes) | build id (32 hex chars) | header length (u32)
    | header (UTF-8 JSON)
    | records: one per group, ``cells`` bytes each
    | exact values (continuous scales only): one per group, ``cells`` float64 each

The build id is random per write. A page read checks it against the index it
was given, so a page is never sliced out of a rebuilt file at the old
offsets. (Inode, size and mtime cannot tell: ext4 reuses a freed inode at once
and its mtime is coarse.)

The header is the index: the scale, the facet columns, the cell count, an
opaque ``layout`` the builder uses to place cells, and one entry per group (its
key, one text value per facet column, and its sort values). Group ``i``'s record
starts at ``data_offset + i * cells`` and its exact values at
``exact_offset + i * cells * 8`` — offsets are derived, never stored, so they
cannot disagree with the data.

A record is one byte per cell: a category code, or a continuous value quantized
to 255 levels (codes 0..254). ``MISSING`` (255) is a cell the group has no row
for. The quantized record colours a thumbnail; the exact values back an
enlarged group's tooltips, so neither needs the source re-read.

The header is strict JSON (no NaN/Infinity literals), because the gallery
parses it in the browser. The format takes plain Python values only, checked
by exact type (a numpy float64 or str_ is a subclass and is refused too), and
does not guess at pandas/numpy types (NaT, datetime64 and tz offsets each guess
wrong): the builder converts, or the write fails naming what it refused. Reading
needs only the standard library, so the pager never pays a pandas import
(plan-view-plugins-pr4 P2/P4).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import struct
import sys
import tempfile
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO

MAGIC = b"AIWSVC01"
MISSING = 255
LEVELS = 254  # the top quantized code; codes run 0..LEVELS, MISSING is outside
TMP_SUFFIX = ".tmp"  # a build in progress, or one a SIGKILL left behind
_BUILD_ID = 32  # hex chars, so the id can never contain the header's "{"
_HEX_ID = re.compile(rb"[0-9a-f]{%d}" % _BUILD_ID)
_LEN = struct.Struct("<I")
_EXACT = 8  # bytes per exact value (float64)
_JSON_INT = 2**53  # past this JSON.parse rounds an integer


class CacheUnusable(Exception):
    """Missing, unreadable, of another format, or cut short: the caller rebuilds."""


class CacheRebuilt(CacheUnusable):
    """The cache is fine, but it is a later build than the index the read was
    given: the caller refetches the index rather than rebuilding again."""


def transform_hash(transform: Mapping[str, Any]) -> str:
    """A stable digest of the part of a spec that shapes the cache: key order is
    irrelevant, list order is not (``groupby: [x, y]`` is not ``[y, x]``)."""
    canonical = json.dumps(transform, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class CacheKey:
    """What a cache was built from. ``size`` and ``mtime_ns`` stand in for the
    source's version: an edit that changes either is a new file. (An edit that
    keeps both — same size, mtime restored — is a stale hit.)"""

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


def view_ident(args: Mapping[str, Any]) -> str:
    """What names a build for its progress file: the call's own arguments
    (facet_build's and facet_progress's are the same), epoch aside -- no file
    is read, so a poll stays quick. A call that is only a spec's text is
    named by that text."""
    call = {k: v for k, v in args.items() if k != "epoch"}
    if set(call) == {"spec"}:
        return call["spec"]
    return json.dumps(call, sort_keys=True, separators=(",", ":"))


def progress_file(root: Path, spec_text: str) -> Path:
    """Where a build of ``spec_text`` writes its progress lines while it runs,
    for ``facet_progress`` to read back (plan-view-plugins-pr5-finish P10).
    Named by the spec text the gallery sent, which it can ask with before the
    build has answered a key; named as a temp file, so one a SIGKILL leaves
    behind is the cache cap's to sweep."""
    digest = hashlib.sha256(spec_text.encode()).hexdigest()
    return root / f"progress-{digest}{TMP_SUFFIX}"


@dataclass(frozen=True)
class ContinuousScale:
    lo: float
    hi: float
    # no cell has a value (#847/#848 PR 5 P44 row 39): lo and hi are then 0,
    # which a column of 0s also has, so the scale says which it is
    empty: bool = False

    def __post_init__(self) -> None:
        if not (math.isfinite(self.lo) and math.isfinite(self.hi) and self.lo <= self.hi):
            raise ValueError(f"scale range {self.lo}..{self.hi} is not finite and ordered")

    def encode(self, values: Sequence[float | None]) -> bytes:
        span = self.hi - self.lo
        out = bytearray()
        for v in values:
            if v is not None:
                if type(v) not in (int, float):
                    raise ValueError(f"cell value {v!r} is a {type(v).__name__}, not a number")
                try:
                    v = float(v)
                except OverflowError:
                    raise ValueError(f"cell value {v} does not fit a float") from None
            # pandas' missing cell is NaN; +/-inf is missing too, as PR 2's q8 wire
            # codes it, so a thumbnail and the full view paint the same pixels
            if v is None or not math.isfinite(v):
                out.append(MISSING)
            elif span <= 0 or not math.isfinite(span) or v <= self.lo:
                # a range too wide for a float (hi - lo = inf) codes 0, as _q8 does
                out.append(0)
            elif v >= self.hi:
                out.append(LEVELS)
            else:
                out.append(round((v - self.lo) / span * LEVELS))
        return bytes(out)

    def decode(self, record: bytes) -> list[float | None]:
        # A range too wide for a float gives step = inf, and present cells then
        # decode to NaN -- as the browser's q8 arithmetic does with such a range.
        step = (self.hi - self.lo) / LEVELS
        return [
            None if b == MISSING else self.hi if b == LEVELS else self.lo + b * step for b in record
        ]

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": "continuous", "lo": self.lo, "hi": self.hi}
        return {**out, "empty": True} if self.empty else out


@dataclass(frozen=True)
class CategoryScale:
    labels: Sequence[str]

    def __post_init__(self) -> None:
        if not all(type(label) is str for label in self.labels):
            raise ValueError(f"category labels {list(self.labels)!r} are not all text")
        if len(self.labels) > LEVELS + 1:
            raise ValueError(
                f"{len(self.labels)} categories; one byte per cell holds at most {LEVELS + 1}"
            )

    def encode(self, values: Sequence[str | float | None]) -> bytes:
        code = {label: i for i, label in enumerate(self.labels)}
        out = bytearray()
        for v in values:
            if _is_missing(v):  # pandas fills a missing text cell with NaN
                out.append(MISSING)
            elif type(v) is str and v in code:
                out.append(code[v])
            else:
                raise ValueError(
                    f"value {v!r} ({type(v).__name__}) is not one of the scale's categories"
                )
        return bytes(out)

    def decode(self, record: bytes) -> list[str | None]:
        try:
            return [None if b == MISSING else self.labels[b] for b in record]
        except IndexError:
            raise CacheUnusable("a cell's category code is outside the labels") from None

    def to_json(self) -> dict[str, Any]:
        return {"kind": "category", "labels": list(self.labels)}


def _is_missing(v: object) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


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
    build_id: bytes  # which write this index describes; see the module docstring
    # a zoned facet column's zone, for the gallery's labels (#847/#848 P14);
    # a cache written before it has none, which only leaves the zone unnamed
    zones: Mapping[str, str] = MappingProxyType({})
    # every column of the frame the cache was built from: its name, its kind
    # (number / text / date) and whether it holds one value per group -- what
    # a gallery offers to sort by (plan-view-plugins-pr5-finish P4)
    columns: list[dict[str, Any]] = field(default_factory=list)
    # what the cache was built from -- the source (path, size, mtime), the
    # transform and the x / y channels -- so a stack can read the same rows
    # (P5); never sent to the browser
    origin: dict[str, Any] = field(default_factory=dict)

    @property
    def exact_offset(self) -> int:
        return self.data_offset + len(self.groups) * self.cells

    @property
    def size(self) -> int:
        exact = len(self.groups) * self.cells * _EXACT
        return self.exact_offset + (exact if isinstance(self.scale, ContinuousScale) else 0)


def _sort_value(field: str, v: Any) -> Any:
    """A sort value as strict JSON the browser reads back exactly: a non-finite
    float (pandas' missing NaN, or +/-inf) is null. Only None, bool, int within
    +/-2**53, float and str are taken; anything else (a date, a numpy scalar) is
    refused by field name, for the builder to convert. Types are exact: a numpy
    float64 is a float subclass and is refused, NaN or not."""
    if type(field) is not str:
        raise ValueError(f"sort field name {field!r} is not text (JSON keys are text)")
    if v is not None and type(v) not in (bool, int, float, str):
        raise ValueError(f"sort value {field!r} is a {type(v).__name__}, not a plain JSON scalar")
    if type(v) is float and not math.isfinite(v):
        return None
    if type(v) is int and abs(v) > _JSON_INT:
        raise ValueError(f"sort value {field!r} is {v}, past what JSON.parse keeps exact")
    return v


def _scale_from_json(raw: Mapping[str, Any]) -> Scale:
    if raw["kind"] == "category":
        return CategoryScale(labels=raw["labels"])
    return ContinuousScale(lo=raw["lo"], hi=raw["hi"], empty=raw.get("empty", False))


def write_cache(
    path: Path,
    *,
    scale: Scale,
    facet: Sequence[str],
    cells: int,
    layout: Mapping[str, Any],
    groups: Sequence[Group],
    zones: Mapping[str, str] = MappingProxyType({}),
    columns: Sequence[Mapping[str, Any]] = (),
    origin: Mapping[str, Any] | None = None,
) -> None:
    """Write the whole cache or nothing. The file appears at ``path`` only when
    complete (a temp file in the same dir, then ``os.replace``); a failed write
    removes its temp file, and one a SIGKILL leaves ends in ``TMP_SUFFIX`` for
    the cache cap to sweep."""
    if cells < 1:
        raise ValueError(f"a group needs at least one cell, not cells={cells}")
    if not all(type(c) is str for c in facet):
        raise ValueError(f"facet columns {tuple(facet)!r} are not all text")
    if not all(c in facet and type(z) is str for c, z in zones.items()):
        raise ValueError(f"zones {dict(zones)!r} must name facet columns, each with a text zone")
    records = []
    exact = array("d")
    for g in groups:
        if len(g.key) != len(facet):
            raise ValueError(
                f"group {g.key!r} has {len(g.key)} key values, the facet is {tuple(facet)!r}"
            )
        if not all(type(k) is str for k in g.key):
            # a marking value is an opaque string (Q6): 12 would never match "12"
            types = ", ".join(type(k).__name__ for k in g.key)
            raise ValueError(f"group key {g.key!r} is not all str (it is {types})")
        if len(g.values) != cells:
            raise ValueError(f"group {g.key!r} has {len(g.values)} cells, the cache has {cells}")
        records.append(scale.encode(g.values))
        if isinstance(scale, ContinuousScale):
            exact.extend(math.nan if v is None else float(v) for v in g.values)
    header = json.dumps(
        {
            "scale": scale.to_json(),
            "facet": list(facet),
            "zones": dict(zones),
            "cells": cells,
            "layout": dict(layout),
            "columns": [dict(c) for c in columns],
            "origin": dict(origin or {}),
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
            f.write(secrets.token_hex(_BUILD_ID // 2).encode())
            f.write(_LEN.pack(len(header)))
            f.write(header)
            for record in records:
                f.write(record)
            f.write(exact.tobytes())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _parse_index(raw: Any, data_offset: int, build_id: bytes) -> CacheIndex:
    # Only we write this header, so it is not re-validated field by field: a
    # missing key or most wrong types fail as KeyError/TypeError (CacheUnusable
    # in read_index), and the size check catches a header whose counts do not
    # match the file. ``cells`` is checked because every offset is computed from it.
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
        build_id=build_id,
        zones=raw.get("zones", {}),
        columns=raw["columns"],
        origin=raw["origin"],
    )


def _open_checked(path: Path) -> tuple[BinaryIO, bytes, int]:
    """Open a cache and read its magic and build id: (file, build id, size)."""
    try:
        f = path.open("rb")
    except OSError as e:  # missing, a directory, unreadable
        raise CacheUnusable(f"{path}: {e.strerror or e}") from None
    try:
        size = os.fstat(f.fileno()).st_size
        magic, build_id = f.read(len(MAGIC)), f.read(_BUILD_ID)
    except OSError as e:  # an I/O error after the open: close the file, don't leak it
        f.close()
        raise CacheUnusable(f"{path}: {e.strerror or e}") from None
    if magic != MAGIC:
        f.close()
        raise CacheUnusable(f"{path}: not a facet cache in this format")
    if not _HEX_ID.fullmatch(build_id):  # also a short one, cut before the header
        f.close()
        raise CacheUnusable(f"{path}: its build id is not {_BUILD_ID} hex characters")
    return f, build_id, size


def read_index(path: Path) -> CacheIndex:
    """Read the header only. Raises ``CacheUnusable`` for anything short of a
    complete cache in this format, including a last record cut short — a
    gallery sorts from the index before it reads any page."""
    f, build_id, size = _open_checked(path)
    with f:
        try:
            head = f.read(_LEN.size)
            if len(head) != _LEN.size:
                raise CacheUnusable(f"{path}: cut short in its header")
            (n,) = _LEN.unpack(head)
            raw = json.loads(f.read(n))
            index = _parse_index(raw, len(MAGIC) + _BUILD_ID + _LEN.size + n, build_id)
        # JSONDecodeError is a ValueError; a huge int in the range overflows a float,
        # and a header nested too deep exhausts the decoder's recursion.
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as e:
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            raise CacheUnusable(f"{path}: unreadable header ({reason})") from None
    if size != index.size:
        raise CacheUnusable(f"{path}: {size} bytes, not the {index.size} expected")
    return index


def _open_same(path: Path, index: CacheIndex) -> BinaryIO:
    f, build_id, size = _open_checked(path)
    if build_id != index.build_id:
        f.close()
        raise CacheRebuilt(f"{path}: rebuilt after its index was read")
    if size != index.size:  # same build, fewer bytes: cut short since
        f.close()
        raise CacheUnusable(f"{path}: {size} bytes, not the {index.size} its index expects")
    return f


def _check_positions(index: CacheIndex, positions: Sequence[int]) -> None:
    n = len(index.groups)
    for p in positions:
        if type(p) is not int:
            raise TypeError(f"group position {p!r} is not an int")
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
