"""``facet_build {"spec"}`` (plan-view-plugins-pr4 P3/P6): a ``facet:`` spec
in, the cache a gallery opens out.

Built once per (normalised source path, its size and mtime, and only what
shapes the bytes: the facet columns, the sort field, x / y / color field and
type, the transform), so a second open, or a gallery refetching its index,
reuses it -- sorting the other way or retitling costs no rebuild. The path
keyed is the path read, so the cache can never hold another file's rows;
every build then bounds the cache dir by the spec's ``facet.cache_mb`` (P3).
That dir is shared by every gallery in the sandbox, so the cap a spec names
bounds all of them, not just its own cache.
The answer is ``{"key", "build", "groups", "cells", "built"}``; the progress
lines go to stderr, which the runner hands back with the answer (it does not
stream them).

Imported only when ``facet_build`` runs: it loads pandas, which the pager
commands beside it must not.
"""

from __future__ import annotations

import json
import posixpath
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from chart_view.facet import CacheKey, CacheUnusable, cache_file, read_index, transform_hash
from chart_view.facet.build import build_facet_cache
from chart_view.facet.cap import enforce_cap
from chart_view.facet.pager import _used
from chart_view.query import _kinds
from chart_view.sources import read_source
from chart_view.spec import parse_spec, spec_errors
from chart_view.transforms import apply_transforms

DEFAULT_CAP_MB = 500
_MB = 1024 * 1024


class _Refused(ValueError):
    """The spec cannot open as a gallery; the message says why."""


def _channel(spec: dict[str, Any], name: str) -> dict[str, Any]:
    # the schema requires a grid's x, y and colour fields (#855's markChannels),
    # and a spec is checked against it before this runs
    channel = spec["encoding"][name]
    if "aggregate" in channel:
        raise _Refused(
            f"encoding.{name} aggregates, which a facet gallery cannot do per group: aggregate"
            " in transform: with the facet columns in its groupby"
        )
    return channel


def _key(root: Path, spec: dict[str, Any]) -> tuple[CacheKey, str]:
    """The cache key, and the source path it was keyed on -- the one to read."""
    source = spec["source"]
    if not isinstance(source, str):
        raise _Refused("a facet gallery reads a table file; source: {entity: ...} is not one")
    # one file, one cache: "/data/w.csv", "./data/w.csv" and "data//w.csv" are it
    relative = posixpath.normpath(source.lstrip("/"))
    try:
        stat = (root / relative).stat()
    except OSError:
        raise _Refused(f"source {source!r} is not a file in the workspace") from None
    # only what shapes the cache's bytes: not the sort ORDER (the gallery sorts
    # the index in hand), not titles or colour schemes, not the cap
    facet = spec["facet"]
    shape = {
        "facet": {"field": facet["field"], "sort": facet.get("sort", {}).get("field")},
        "encoding": {
            c: {"field": spec["encoding"][c]["field"], "type": spec["encoding"][c]["type"]}
            for c in ("x", "y", "color")
        },
        "transform": spec.get("transform", []),
    }
    key = CacheKey(
        source_path=relative,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        transform_hash=transform_hash(shape),
    )
    return key, relative


def _answer(path: Path, key: CacheKey, built: bool) -> dict[str, Any]:
    index = read_index(path)
    return {
        "key": key.digest(),
        "build": index.build_id.decode("ascii"),
        "groups": len(index.groups),
        "cells": index.cells,
        "built": built,
    }


def _build(text: str) -> dict[str, Any]:
    spec = parse_spec(text)
    errors = spec_errors(spec)
    if errors:
        raise _Refused("\n".join(errors))
    if "facet" not in spec:
        raise _Refused("facet_build needs a spec with facet:")
    x, y, color = (_channel(spec, c) for c in ("x", "y", "color"))
    channels: list[tuple[str, Mapping[str, Any]]] = [("x", x), ("y", y), ("color", color)]
    workspace = Path.cwd()
    key, relative = _key(workspace, spec)
    views = Path.home() / ".cache" / "views"
    views.mkdir(parents=True, exist_ok=True)
    path = cache_file(views, key)
    try:
        answer = _answer(path, key, built=False)
    except CacheUnusable:
        pass  # none yet, or unusable: build it
    else:
        _used(path)  # reused is used: the cap evicts the least recently used
        return answer

    facet = spec["facet"]
    fields = facet["field"] if isinstance(facet["field"], list) else [facet["field"]]
    # read the path the key was made from: folding `..` as text and letting the
    # OS walk a symlink first can name two different files
    frame = apply_transforms(read_source(workspace, relative), spec.get("transform", []))
    build_facet_cache(
        frame,
        facet=fields,
        x=x["field"],
        y=y["field"],
        value=color["field"],
        sort=[facet["sort"]["field"]] if "sort" in facet else [],
        path=path,
        x_type=x["type"],
        y_type=y["type"],
        # the colour's kind as query sends a grid's (q8 = a ramp, else categories)
        continuous=_kinds("grid", channels)[color["field"]] == "q8",
        progress=lambda line: print(line, file=sys.stderr),
    )
    enforce_cap(views, cap_bytes=facet.get("cache_mb", DEFAULT_CAP_MB) * _MB, keep=path)
    return _answer(path, key, built=True)


def run(text: str) -> int:
    """Every refusal here (SpecError, SourceError, TransformError, BuildError,
    _Refused) is a ValueError, which ``facet.cli.run`` answers as exit 2."""
    json.dump(_build(text), sys.stdout, separators=(",", ":"))
    return 0
