"""`transform:` — filter, aggregate and diff over a frame, in spec order.

The spec has already passed the schema (`chart_view.spec`), so the shapes here
are trusted; what is not is the DATA — a column the spec names may not exist, and
a query may not evaluate. Both raise `TransformError` naming the column or the
expression, because `validate` hands that line to the AI that wrote the spec.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


class TransformError(ValueError):
    """A transform cannot run against this data."""


def need_columns(df: pd.DataFrame, *columns: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        have = ", ".join(map(str, df.columns))
        raise TransformError(f"no column {missing[0]!r} (columns: {have})")


def _query(df: pd.DataFrame, expr: str, what: str) -> pd.Series:
    """The rows `expr` selects, as a boolean mask aligned with `df`."""
    try:
        mask = df.eval(expr)
    except Exception as e:  # pandas raises many types for a bad expression
        raise TransformError(f"{what} {expr!r} does not evaluate: {e}") from e
    if not isinstance(mask, pd.Series) or not pd.api.types.is_bool_dtype(mask):
        raise TransformError(f"{what} {expr!r} must be a true/false test on each row")
    return mask


_COMPARE = {
    "lt": lambda s, v: s < v,
    "lte": lambda s, v: s <= v,
    "gt": lambda s, v: s > v,
    "gte": lambda s, v: s >= v,
}


def _predicate(df: pd.DataFrame, pred: Mapping[str, Any]) -> pd.Series:
    field = pred["field"]
    need_columns(df, field)
    s = df[field]
    mask = pd.Series(True, index=df.index)
    if "equal" in pred:
        mask &= s == pred["equal"]
    if "oneOf" in pred:
        mask &= s.isin(pred["oneOf"])
    if "range" in pred:
        low, high = pred["range"]
        mask &= (s >= low) & (s <= high)
    for op, compare in _COMPARE.items():
        if op in pred:
            try:
                mask &= compare(s, pred[op])
            except TypeError as e:
                raise TransformError(f"filter on {field!r}: {op} {pred[op]!r} — {e}") from e
    if "valid" in pred:
        mask &= s.notna() if pred["valid"] else s.isna()
    return mask


def row_mask(df: pd.DataFrame, test: str | Mapping[str, Any], what: str = "filter") -> pd.Series:
    """The rows a filter (a pandas query, or a field predicate) keeps."""
    if isinstance(test, str):
        return _query(df, test, what)
    return _predicate(df, test)


def _rate(s: pd.Series) -> float:
    # Share of truthy rows over EVERY row of the group — a missing value is a
    # row that did not pass, not a row to leave out of the denominator.
    passed = sum(bool(v) for v in s if not pd.isna(v))
    return passed / len(s) if len(s) else float("nan")


_ALL = "__all__"


def aggregate(
    df: pd.DataFrame, items: Sequence[Mapping[str, Any]], groupby: Sequence[str]
) -> pd.DataFrame:
    """One row per group — without `groupby`, one row for the frame (none for an
    empty one); a column per item."""
    need_columns(df, *groupby, *(i["field"] for i in items if i.get("field")))
    clash = [i["as"] for i in items if i["as"] in groupby]
    if clash:
        raise TransformError(
            f"aggregate `as: {clash[0]!r}` is also a groupby column — name it apart"
        )
    keys = list(groupby) or [_ALL]
    frame = df if groupby else df.assign(**{_ALL: 0})
    grouped = frame.groupby(keys, dropna=False, sort=True)
    out = pd.DataFrame({i["as"]: _grouped(frame, grouped, keys, i) for i in items})
    out = out.reset_index()
    return out if groupby else out.drop(columns=_ALL)


def _grouped(df: pd.DataFrame, grouped: Any, keys: list[str], item: Mapping[str, Any]) -> Any:
    op, field = item["op"], item.get("field")
    if op == "count":
        return grouped.size() if field is None else grouped[field].count()
    if op == "rate":
        return grouped[field].agg(_rate)
    numbers = pd.to_numeric(df[field], errors="coerce")
    return getattr(numbers.groupby([df[k] for k in keys], dropna=False), op)()


def _diff(df: pd.DataFrame, t: Mapping[str, Any]) -> pd.DataFrame:
    by, items, groupby = t["diff"]["by"], t["aggregate"], list(t.get("groupby", []))
    need_columns(df, by)
    of = aggregate(df[df[by] == t["diff"]["of"]], items, groupby)
    minus = aggregate(df[df[by] == t["diff"]["minus"]], items, groupby)
    names = [i["as"] for i in items]
    if not groupby:
        return of[names] - minus[names]
    both = of.merge(minus, on=groupby, how="inner", suffixes=("", " minus"))
    for name in names:
        both[name] = both[name] - both[f"{name} minus"]
    return both[[*groupby, *names]]


def apply_transforms(df: pd.DataFrame, transforms: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """`df` after each transform in order."""
    for t in transforms:
        if "filter" in t:
            df = df[row_mask(df, t["filter"])].reset_index(drop=True)
        elif "diff" in t:
            df = _diff(df, t)
        else:
            df = aggregate(df, t["aggregate"], t.get("groupby", []))
    return df
