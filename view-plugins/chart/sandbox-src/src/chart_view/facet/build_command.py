"""``facet_build {"spec"}`` (plan-view-plugins-pr4 P3/P6): a ``facet:`` spec
in, the cache a gallery opens out.

Built once per (source path, size, mtime, the spec's facet / encoding /
transform), so a second open, or a gallery refetching its index, reuses it;
every build then bounds the cache dir by the spec's ``facet.cache_mb`` (P3).
The answer is ``{"key", "build", "groups", "cells", "built"}``; the progress
lines go to stderr, which the runner hands back with the answer (it does not
stream them).

Imported only when ``facet_build`` runs: it loads pandas, which the pager
commands beside it must not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from chart_view.facet import CacheKey, CacheUnusable, cache_file, read_index, transform_hash
from chart_view.facet.build import build_facet_cache
from chart_view.facet.cap import enforce_cap
from chart_view.sources import read_source
from chart_view.spec import parse_spec, spec_errors
from chart_view.transforms import apply_transforms

DEFAULT_CAP_MB = 500
_MB = 1024 * 1024


class _Refused(ValueError):
    """The spec cannot open as a gallery; the message says why."""


def _channel(spec: dict[str, Any], name: str) -> dict[str, Any]:
    # the schema makes every channel present a {field, type}; only absence is left
    channel = spec["encoding"].get(name)
    if channel is None:
        raise _Refused(f"a facet gallery draws each grid by encoding.{name}: the spec has none")
    return channel


def _key(root: Path, spec: dict[str, Any]) -> CacheKey:
    source = spec["source"]
    if not isinstance(source, str):
        raise _Refused("a facet gallery reads a table file; source: {entity: ...} is not one")
    try:
        stat = (root / source.lstrip("/")).stat()
    except OSError:
        raise _Refused(f"source {source!r} is not a file in the workspace") from None
    facet = {k: v for k, v in spec["facet"].items() if k != "cache_mb"}  # the cap shapes no cache
    shape = {
        "facet": facet,
        "encoding": {c: spec["encoding"][c] for c in ("x", "y", "color")},
        "transform": spec.get("transform", []),
    }
    return CacheKey(
        source_path=source,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        transform_hash=transform_hash(shape),
    )


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
    workspace = Path.cwd()
    key = _key(workspace, spec)
    views = Path.home() / ".cache" / "views"
    views.mkdir(parents=True, exist_ok=True)
    path = cache_file(views, key)
    try:
        return _answer(path, key, built=False)
    except CacheUnusable:
        pass  # none yet, or unusable: build it

    facet = spec["facet"]
    fields = facet["field"] if isinstance(facet["field"], list) else [facet["field"]]
    frame = apply_transforms(read_source(workspace, spec["source"]), spec.get("transform", []))
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
        progress=lambda line: print(line, file=sys.stderr),
    )
    enforce_cap(views, cap_bytes=facet.get("cache_mb", DEFAULT_CAP_MB) * _MB, keep=path)
    return _answer(path, key, built=True)


def run(text: str) -> int:
    """Every refusal here (SpecError, SourceError, TransformError, BuildError,
    _Refused) is a ValueError, which ``facet.cli.run`` answers as exit 2."""
    json.dump(_build(text), sys.stdout, separators=(",", ":"))
    return 0
