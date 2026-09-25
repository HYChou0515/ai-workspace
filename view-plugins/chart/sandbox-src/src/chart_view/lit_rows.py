"""``lit_rows`` — the rows a marking lights, with every column, as CSV
(plan-view-plugins-pr5-finish P7, "save as table").

    launch lit_rows '{"view": <path>, "columns": {<column>: [<value>, …]}}'
    launch lit_rows '{"view": <path>, "marking": <path of a .markings/<name>.json>}'

``view`` is the view the action was taken in: a view file (its ``source:`` —
or, for an entity view, its ``entity:`` — and, for ``view: chart``, its
``transform:``), or a table file itself. The rows are lit by the platform's
one rule, the SPA's ``isLit``: a row shares at least one column with the
marking, and on every shared column its value, as marking text (``canon``), is
in that column's set.

Exit 0 with ``{"rows": n, "csv": text}`` on stdout; exit 2 with one sentence
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


def _is_marking(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(v, list) and all(isinstance(x, str) for x in v) for v in value.values()
    )


def _args(raw: str) -> tuple[str, dict[str, list[str]] | str]:
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as e:  # nested past the decoder
        raise _Refused(f"argument is not JSON this command can read: {e}") from None
    if (
        not isinstance(args, dict)
        or not isinstance(args.get("view"), str)
        or set(args) not in ({"view", "columns"}, {"view", "marking"})
    ):
        raise _Refused('lit_rows takes {"view"} and exactly one of {"columns"} / {"marking"}')
    if "marking" in args:
        if not isinstance(args["marking"], str):
            raise _Refused("marking must be the path of a marking file")
        return args["view"], args["marking"]
    if not _is_marking(args["columns"]):
        raise _Refused("columns must map each column to a list of text values")
    return args["view"], args["columns"]


def _marking_file(root: Path, path: str) -> dict[str, list[str]]:
    try:
        doc = json.loads(inside_workspace(root, path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise _Refused("the marking is no longer in the workspace") from None
    columns = doc.get("columns") if isinstance(doc, dict) else None
    if not _is_marking(columns):
        raise _Refused("the marking's file does not hold a marking")
    assert isinstance(columns, dict)
    return columns


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


def lit(frame: pd.DataFrame, columns: dict[str, list[str]]) -> pd.DataFrame:
    """`frame`'s rows the marking lights (the SPA's `isLit`), every column."""
    shared = [c for c in columns if c in frame.columns]
    if not shared:
        raise _Refused("the view has no column in common with the marking")
    mask = pd.Series(True, index=frame.index)
    for c in shared:
        mask &= frame[c].map(canon).isin(set(columns[c]))
    rows = frame[mask]
    if rows.empty:
        raise _Refused("no row of the view is lit by the marking")
    return rows


def run(raw: str) -> int:
    root = Path.cwd()
    try:
        view, marking = _args(raw)
        columns = _marking_file(root, marking) if isinstance(marking, str) else marking
        rows = lit(view_frame(root, view), columns)
    except (_Refused, SpecError, SourceError, TransformError) as e:
        print(str(e), file=sys.stderr)
        return 2
    json.dump({"rows": len(rows), "csv": rows.to_csv(index=False)}, sys.stdout)
    return 0
