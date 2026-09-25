"""`transform:` — filter, aggregate and diff over a frame, in spec order.

The spec has already passed the schema (`chart_view.spec`), so the shapes here
are trusted; what is not is the DATA — a column the spec names may not exist, and
a query may not evaluate. Both raise `TransformError` naming the column or the
expression, because `validate` hands that line to the AI that wrote the spec.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from chart_view.wire import holds_containers, unhashable_as_text


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


def _comparable(s: pd.Series, field: str) -> pd.Series:
    """`s` as a predicate compares it: a list cell as its marking text (what a
    highlight or a marking holds), and a column of date / datetime objects (a
    parquet date32 column, an entity's dates — some left as date text by YAML)
    as datetimes, so "2024-01-01" names a day there as it does in a datetime
    column: a date object is never equal to text. Zoned objects are read at
    UTC, as the chart reads zone-less text (a typed zoned column keeps its
    zone)."""
    s = unhashable_as_text(s)
    if s.dtype != object:
        return s
    kind = pd.api.types.infer_dtype(s, skipna=True)
    if kind in ("date", "datetime", "datetime64"):  # datetime64: numpy instants as objects
        read = _at_utc
    elif kind == "mixed" and s.map(_is_instant).any():
        read = _instant_cell  # dates with some left as text
    else:
        return s
    try:  # microseconds: 0001 to 9999, and a fraction of a second, fit
        return s.map(read).astype("datetime64[us]")
    except OverflowError as e:  # 0001-01-01 at +08:00 is before year 1 at UTC
        raise TransformError(f"{field!r}: an instant out of range at UTC ({e})") from e
    except (TypeError, ValueError):  # a cell that is no date: not a date column
        return s


def _is_instant(v: Any) -> bool:
    return isinstance(v, dt.date | np.datetime64)


def _instant_cell(v: Any) -> Any:
    """A cell of a date column that YAML left partly as text."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if _is_instant(v):
        return _at_utc(v)
    if isinstance(v, str):
        return _at_utc(pd.Timestamp(v))
    raise TypeError(f"{v!r} is no date")


def _read_instant(value: Any, dtype: Any, field: str, op: str) -> list[Any]:
    """A predicate's value on a datetime column: the instants it names there.

    Text is a date, read as the column holds instants (a zone-less time in a
    zoned column's zone; one the zone had twice names both, one it never had
    names none); a number is epoch ms, as a temporal datum is. Anything else —
    a bool, text that is no date, an empty text — is refused by name."""
    try:  # a bool is a TypeError here: pandas reads no bool as a time
        stamp = pd.Timestamp(value) if isinstance(value, str) else pd.Timestamp(value, unit="ms")
    except (TypeError, ValueError, OverflowError):
        stamp = pd.NaT
    if not isinstance(stamp, pd.Timestamp):  # NaT: "", "NaT", or no date at all
        raise TransformError(f"filter on {field!r}: {op} {value!r} is not a date")
    zone = getattr(dtype, "tz", None)
    if zone is None:
        return [_at_utc(stamp)]
    if stamp.tzinfo is not None:  # an instant: a zoned column compares instants
        return [stamp]
    both = [stamp.tz_localize(zone, nonexistent="NaT", ambiguous=a) for a in (True, False)]
    return list(dict.fromkeys(b for b in both if isinstance(b, pd.Timestamp)))


def _at_utc(v: Any) -> Any:
    if getattr(v, "tzinfo", None) is None:
        return v
    return v.astimezone(dt.UTC).replace(tzinfo=None)


def _predicate(df: pd.DataFrame, pred: Mapping[str, Any]) -> pd.Series:
    field = pred["field"]
    need_columns(df, field)
    # (what the author wrote, the comparison, the value)
    orders = [(op, op, v) for op, v in pred.items() if op in _COMPARE]
    if "range" in pred:
        low, high = pred["range"]
        orders += [("range", "gte", low), ("range", "lte", high)]
    if orders and holds_containers(df[field]):  # before the column is made text
        # Against text, a list's marking text would be ordered alphabetically.
        raise TransformError(
            f"filter on {field!r}: {field!r} holds lists or mappings, which have no order —"
            " compare them with equal or oneOf"
        )
    # A list cell compared as a numpy array compared as its only item (one
    # element) or raised on `==` (more), never as its text; see _comparable.
    s = _comparable(df[field], field)
    dates = pd.api.types.is_datetime64_any_dtype(s)
    mask = pd.Series(True, index=df.index)
    if "equal" in pred:
        value = pred["equal"]
        mask &= s.isin(_read_instant(value, s.dtype, field, "equal")) if dates else s == value
    if "oneOf" in pred:
        values = pred["oneOf"]
        if dates:  # the schema has no null here: `valid: false` names the missing rows
            values = [i for v in values for i in _read_instant(v, s.dtype, field, "oneOf")]
        mask &= s.isin(values)
    for name, op, value in orders:
        shown = pred["range"] if name == "range" else value
        if dates:
            instants = _read_instant(value, s.dtype, field, name)
            if len(instants) != 1:  # a local time the zone had twice, or never
                raise TransformError(
                    f"filter on {field!r}: {name} {shown!r} is no single time in"
                    f" {getattr(s.dtype, 'tz', 'UTC')}"
                )
            value = instants[0]
        try:
            mask &= _COMPARE[op](s, value)
        except TypeError as e:  # text, a list or a date against a number
            raise TransformError(f"filter on {field!r}: {name} {shown!r} — {e}") from e
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
    # A list key (an entity or parquet list field) has no hash to group by.
    frame = df.assign(**{k: unhashable_as_text(df[k]) for k in groupby})
    frame = frame if groupby else frame.assign(**{_ALL: 0})
    grouped = frame.groupby(keys, dropna=False, sort=True)
    out = pd.DataFrame({i["as"]: _grouped(frame, grouped, keys, i) for i in items})
    out = out.reset_index()
    return out if groupby else out.drop(columns=_ALL)


def _grouped(df: pd.DataFrame, grouped: Any, keys: list[str], item: Mapping[str, Any]) -> Any:
    op, field = item["op"], item.get("field")
    if op == "count":
        return grouped.size() if field is None else grouped[field].count()
    if (
        op == "rate"
        and df[field].map(lambda v: isinstance(v, str) or not pd.api.types.is_scalar(v)).any()
    ):
        # bool("no") is True: over text, a rate would count every answer as
        # yes; a list (an entity field can hold one) has no truth at all.
        raise TransformError(
            f"rate over {field!r} needs true/false or 0/1 values, not text or lists"
        )
    if op == "rate":
        return grouped[field].agg(_rate)
    numbers = pd.to_numeric(df[field], errors="coerce")
    return getattr(numbers.groupby([df[k] for k in keys], dropna=False), op)()


def _diff(df: pd.DataFrame, t: Mapping[str, Any]) -> pd.DataFrame:
    by, items, groupby = t["diff"]["by"], t["aggregate"], list(t.get("groupby", []))
    need_columns(df, by)
    side = _comparable(df[by], by)  # a list side by its marking, a date by its day
    of = aggregate(df[side == t["diff"]["of"]], items, groupby)
    minus = aggregate(df[side == t["diff"]["minus"]], items, groupby)
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
