"""``lit_rows`` — the rows a marking lights, with every column, as CSV
(plan-view-plugins-pr5-finish P7, "save as table").

    launch lit_rows '{"view": <path>, "keys": [<column>, …], "rows": [[<value>, …], …]}'
    launch lit_rows '{"view": <path>, "marking": <path of a .markings/<name>.json>}'

A marking is the picked key TUPLES (#861 D1): ``keys`` names the columns, and
each of ``rows`` is one picked row's values on them, in that order, as marking
text (``canon``). The file form holds the same two fields.

``view`` is the view the action was taken in: a view file (its ``source:`` —
or, for an entity view, its ``entity:`` — and, for ``view: chart``, its
``transform:``), or a table file itself. The rows are lit by the platform's
one rule, the SPA's ``isLit``: a row shares at least one key with the marking,
and its values on the keys it shares are one of the picked tuples projected
onto those keys (#861 D2) — an exact tuple match when it has every key.

Exit 0 with ``{"rows": n, "csv": text, "marking": {"keys", "rows"}}`` (the
marking it lit by) on stdout; exit 2 with one sentence
on stderr — a wrong call, a view with nothing to read, no column in common, or
no row lit. The CSV is not written here: the platform writes it through its
file facade, so the workspace quota and the user's permission apply.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from chart_view.sources import SourceError, inside_workspace, read_source
from chart_view.spec import SpecError, parse_spec, spec_errors
from chart_view.transforms import TransformError, apply_transforms
from chart_view.wire import canon

_TABLES = (".csv", ".tsv", ".parquet")


class _Refused(ValueError):
    """The one sentence the command answers with on stderr."""


#: A marking: its key columns, and the picked tuples on them.
Marking = tuple[list[str], list[list[str]]]


def _marking(keys: Any, rows: Any) -> Marking | None:
    """``(keys, rows)`` when they are a marking — distinct text keys, and each
    row one text value per key — else None."""
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        return None
    if len(set(keys)) != len(keys) or not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, list) or len(row) != len(keys):
            return None
        if not all(isinstance(v, str) for v in row):
            return None
    return keys, rows


def _args(raw: str) -> tuple[str, Marking | str]:
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as e:  # nested past the decoder
        raise _Refused(f"argument is not JSON this command can read: {e}") from None
    if (
        not isinstance(args, dict)
        or not isinstance(args.get("view"), str)
        or set(args) not in ({"view", "keys", "rows"}, {"view", "marking"})
    ):
        raise _Refused('lit_rows takes {"view"} and either {"keys", "rows"} or {"marking"}')
    if "marking" in args:
        if not isinstance(args["marking"], str):
            raise _Refused("marking must be the path of a marking file")
        return args["view"], args["marking"]
    marking = _marking(args["keys"], args["rows"])
    if marking is None:
        raise _Refused(
            "keys must be distinct column names, and each row one text value per key, in order"
        )
    return args["view"], marking


def _marking_file(root: Path, path: str) -> Marking:
    try:
        doc = json.loads(inside_workspace(root, path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise _Refused("the marking is no longer in the workspace") from None
    marking = _marking(doc.get("keys"), doc.get("rows")) if isinstance(doc, dict) else None
    if marking is None:
        raise _Refused("the marking's file does not hold a marking")
    return marking


def view_frame(root: Path, view: str) -> pd.DataFrame:
    """The rows `view` draws from: its source read, its transforms applied."""
    if Path(view).suffix.lower() in _TABLES:
        return read_source(root, view)
    try:
        text = inside_workspace(root, view).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise _Refused(f"{view} is not a readable view file in the workspace") from None
    doc = parse_spec(text)
    if not isinstance(doc, dict):
        raise _Refused(f"{view} is not a view file")
    if doc.get("view") == "chart":
        errors = spec_errors(doc)
        if errors:
            raise _Refused(f"{view} is not a valid chart: {errors[0]}")
        return apply_transforms(read_source(root, doc["source"]), doc.get("transform", []))
    source = doc.get("source")
    if isinstance(source, str) and source.strip():
        return read_source(root, source.strip())
    entity = doc.get("entity")
    if isinstance(entity, str) and entity:
        return read_source(root, {"entity": entity})
    raise _Refused(f"{view} names no source to take rows from")


def lit(frame: pd.DataFrame, marking: Marking) -> pd.DataFrame:
    """`frame`'s rows the marking lights (the SPA's `isLit`), every column.

    The picked tuples are projected onto the keys the frame has (#861 D2): with
    every key that is the tuples themselves, with fewer it is what contains a
    pick; a row lights when its own values on those keys are one of them."""
    keys, picked = marking
    at = [i for i, k in enumerate(keys) if k in frame.columns]
    if not at:
        raise _Refused("the view has no column in common with the marking")
    projected = {tuple(row[i] for i in at) for row in picked}
    own = zip(*(frame[keys[i]].map(canon) for i in at), strict=True)
    mask = pd.Series([t in projected for t in own], index=frame.index, dtype=bool)
    rows = frame[mask]
    if rows.empty:
        raise _Refused("no row of the view is lit by the marking")
    return rows


def run(raw: str) -> int:
    root = Path.cwd()
    try:
        view, given = _args(raw)
        marking = _marking_file(root, given) if isinstance(given, str) else given
        rows = lit(view_frame(root, view), marking)
    except (_Refused, SpecError, SourceError, TransformError) as e:
        print(str(e), file=sys.stderr)
        return 2
    # `marking`: the one it lit by, as read — the platform checks a chip's save
    # against the tuples that chip sent.
    keys, picked = marking
    answer = {
        "rows": len(rows),
        "csv": rows.to_csv(index=False),
        "marking": {"keys": keys, "rows": picked},
    }
    json.dump(answer, sys.stdout)
    return 0
