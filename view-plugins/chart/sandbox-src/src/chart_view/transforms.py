"""`transform:` — filter, aggregate and diff over a frame, in spec order.

The spec has already passed the schema (`chart_view.spec`), so the shapes here
are trusted; what is not is the DATA — a column the spec names may not exist, and
a query may not evaluate. Both raise `TransformError` naming the column or the
expression, because `validate` hands that line to the AI that wrote the spec.
"""

from __future__ import annotations

import ast
import datetime as dt
import itertools
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from pandas.errors import OutOfBoundsDatetime

from chart_view.instants import (
    DATE_TEXT,
    instant_of_number,
    local_instants,
    parse_date_text,
    with_folds,
)
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
    # Before pandas answers: `<` raises there, but `==` / `in` / `isin` match
    # no row and `!=` every row, with only a FutureWarning.
    named = _zone_less(df, expr, what)
    if named is not None:
        raise TransformError(named)
    try:
        mask = df.eval(expr)
    except Exception as e:  # pandas raises many types for a bad expression
        raise TransformError(f"{what} {expr!r} does not evaluate: {e}") from e
    if not isinstance(mask, pd.Series) or not pd.api.types.is_bool_dtype(mask):
        raise TransformError(f"{what} {expr!r} must be a true/false test on each row")
    return mask


def _zone_less(df: pd.DataFrame, expr: str, what: str) -> str | None:
    """The refusal for a query that compares a zoned column with a time written
    without a zone, by any operator or `isin`, naming both; None when `expr`
    holds no such comparison. pandas raised "Invalid comparison between
    dtype=datetime64[ns, <zone>] and Timestamp" for an order, and answered an
    equality or a membership as if no time matched. The expression is only
    read, never rewritten."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:  # pandas' own syntax (`backticks`): pandas judges it
        return None
    for name, value in _compared(tree):
        if name not in df.columns:
            continue
        zone = getattr(df[name].dtype, "tz", None)
        at = parse_date_text(value)
        if zone is None or at is None or at.tzinfo is not None:
            continue
        example = at.replace(tzinfo=with_folds(zone)).isoformat()
        also = (
            f", or use a field predicate on {name!r}, which reads a time"
            " without a zone in the column's zone"
            if what == "filter"
            else ""
        )
        return (
            f"{what} {expr!r}: {name!r} holds times in {zone} and {value!r} has"
            f" no zone — write the time with its zone, as {example!r}{also}"
        )
    return None


def _compared(tree: ast.AST) -> Iterator[tuple[str, str]]:
    """(a name, a text it is compared with) for each comparison in `tree` — by an
    operator (`ts == 'a'`, `'a' < ts`, `ts in ['a', 'b']`) or `ts.isin([...])`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            pairs = itertools.pairwise([node.left, *node.comparators])
            sides = [side for a, b in pairs for side in ((a, b), (b, a))]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "isin"
        ):
            given = [*node.args[:1], *(k.value for k in node.keywords if k.arg == "values")]
            sides = [(node.func.value, v) for v in given]
        else:
            continue
        for name, other in sides:
            if not isinstance(name, ast.Name):
                continue
            many = isinstance(other, ast.List | ast.Tuple)  # pandas has no sets
            for item in other.elts if many else [other]:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    yield name.id, item.value


_COMPARE = {
    "lt": lambda s, v: s < v,
    "lte": lambda s, v: s <= v,
    "gt": lambda s, v: s > v,
    "gte": lambda s, v: s >= v,
}


# Object columns that may hold dates: `infer_dtype` is one C pass, so a column
# of numbers, or of text that is not all date text, is never read cell by cell.
_MAY_HOLD_DATES = ("date", "datetime", "datetime64", "mixed")


def _comparable(s: pd.Series, field: str) -> pd.Series:
    """`s` as a predicate compares it: a list cell as its marking text (what a
    highlight or a marking holds), and a column of date / datetime objects (a
    parquet date32 column, an entity's dates — some left as date text by YAML —
    or a CSV column whose every cell is date text, as the chart draws it)
    as datetimes, so "2024-01-01" names a day there as it does in a datetime
    column: a date object is never equal to text. It is a date column only when
    every cell is a date (`_date_cell`); zoned cells are read at UTC, as the
    chart reads zone-less text (a typed zoned column keeps its zone)."""
    s = unhashable_as_text(s)
    if s.dtype != object:
        return s
    kind = pd.api.types.infer_dtype(s, skipna=True)
    if kind == "string":  # a CSV date column: dates only if every cell is date text
        if not s.dropna().str.fullmatch(DATE_TEXT).all():
            return s
    elif kind not in _MAY_HOLD_DATES:
        return s
    off: list[Any] = []

    def read(v: Any) -> Any:
        cell = _date_cell(v)
        try:
            return _at_utc(cell)
        except OverflowError:  # 0001-01-01 at +08:00 is before year 1 at UTC
            off.append(v)
            return None

    try:
        cells = s.map(read)
    except TypeError:  # a cell that is no date: not a date column
        return s
    if off:  # all of them dates, so a date past the calendar is refused by name
        raise TransformError(f"{field!r}: {off[0]!r} is outside the calendar at UTC")
    return cells.astype("datetime64[us]")  # 0001 to 9999, and a fraction of a second


def _date_cell(v: Any) -> Any:
    """A date column's cell as a date, None when missing; TypeError for any
    other cell — text only in the spec's date forms (`parse_instant`)."""
    if isinstance(v, str):
        at = parse_date_text(v)
        if at is None:
            raise TypeError(f"{v!r} is no date")
        return at
    if isinstance(v, dt.date | np.datetime64):
        return v
    if pd.api.types.is_scalar(v) and pd.isna(v):
        return None
    raise TypeError(f"{v!r} is no date")


def _at_utc(v: Any) -> Any:
    if getattr(v, "tzinfo", None) is None:
        return v
    return v.astimezone(dt.UTC).replace(tzinfo=None)


def _instants(value: Any, dtype: Any, where: str) -> list[Any]:
    """The instants a spec's value names in a datetime column of `dtype`.

    Text in the spec's date forms or a number (epoch ms), read by
    `chart_view.instants`; anything else is refused by name. A zone-less time
    in a zoned column is a wall time there: one the clocks passed twice names
    both instants, one they skipped names none."""
    at = parse_date_text(value) if isinstance(value, str) else instant_of_number(value)
    if at is None:
        raise TransformError(f"{where} {value!r} is not a date")
    zone = getattr(dtype, "tz", None)
    try:
        if zone is None:
            named = [_at_utc(at)]
        elif at.tzinfo is None:
            named = local_instants(at, zone)
        else:
            named = [at.astimezone(dt.UTC)]
    except OverflowError as e:
        raise TransformError(f"{where} {value!r} is outside the calendar at UTC") from e
    return [pd.Timestamp(i) for i in named]


def _isin(s: pd.Series, instants: Sequence[Any]) -> pd.Series:
    """The rows of datetime column `s` holding one of `instants`. Each is cast to
    the column's own dtype first: one its unit cannot hold exactly, or at all,
    names no row (pandas' `isin` truncated it to the unit, or fell back to
    objects), and so does one past the calendar in the column's zone (pandas
    printed the OverflowError it swallowed there)."""
    zone = getattr(s.dtype, "tz", None)
    held = []
    for at in instants:
        try:
            cast = at.as_unit(s.dt.unit)
            if zone is not None:
                dt.datetime.astimezone(at.to_pydatetime(warn=False), zone)
        except (OutOfBoundsDatetime, OverflowError):
            continue
        if cast == at:
            held.append(cast)
    return s.isin(pd.array(held, dtype=s.dtype))  # the array reads each in the column's zone


def _equals(s: pd.Series, value: Any, where: str) -> pd.Series:
    """The rows of `s` (made `_comparable`) equal to a spec's scalar `value`."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return _isin(s, _instants(value, s.dtype, where))
    return s == value


def _one_of(s: pd.Series, values: Sequence[Any], where: str) -> pd.Series:
    """The rows of `s` (made `_comparable`) holding one of a spec's `values`."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return _isin(s, [i for v in values for i in _instants(v, s.dtype, where)])
    return s.isin(values)


def dates_one_of(
    column: pd.Series, field: str, values: Sequence[Any], where: str
) -> pd.Series | None:
    """The rows of `column` holding one of `values`, read as `oneOf` reads
    them, when `column` is a date column (`_comparable`); None for any other
    column, which a highlight marks by its text. A value that names no date is
    refused by name (`_instants`)."""
    s = _comparable(column, field)
    if not pd.api.types.is_datetime64_any_dtype(s):
        return None
    return _one_of(s, values, where)


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
    where = f"filter on {field!r}:"
    mask = pd.Series(True, index=df.index)
    if "equal" in pred:
        mask &= _equals(s, pred["equal"], f"{where} equal")
    if "oneOf" in pred:
        # the schema has no null here: `valid: false` names missing rows
        mask &= _one_of(s, pred["oneOf"], f"{where} oneOf")
    for name, op, value in orders:
        shown = pred["range"] if name == "range" else value
        if dates:
            instants = _instants(value, s.dtype, f"{where} {name}")
            if len(instants) != 1:  # a wall time the zone had twice, or never
                raise TransformError(
                    f"{where} {name} {shown!r} is no single time in {getattr(s.dtype, 'tz', None)}"
                )
            value = instants[0]
        try:
            mask &= _COMPARE[op](s, value)
        except TypeError as e:  # text, a list or a date against a number
            raise TransformError(f"{where} {name} {shown!r} — {e}") from e
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
    """One row per group that occurs — a category column (a parquet file keeps
    one) makes no row for a combination no row holds (#847/#848 PR 5 P41 row
    22) — and without `groupby`, one row for the frame (none for an
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
    grouped = frame.groupby(keys, dropna=False, sort=True, observed=True)
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
    try:
        return getattr(numbers.groupby([df[k] for k in keys], dropna=False, observed=True), op)()
    except TypeError as e:  # complex numbers have no min / max (P45 row 44)
        raise TransformError(f"{op} over {field!r}: its {numbers.dtype} values have none") from e


_INT64 = np.iinfo(np.int64)


def _minus(a: pd.Series, b: pd.Series) -> pd.Series:
    """`a - b` in a type the difference cannot wrap in (#847/#848 PR 5 P44
    row 38, P45 row 44). A groupby keeps a min / max -- and a sum its values
    fit -- in the column's own type, so subtracted there uint32 0 - 7 was
    4294967289, int8 127 - (-128) was -1, float16 60000 - (-60000) was inf and
    true - false raised. Integers and true/false are subtracted exactly, as
    Python integers: an int64 column when int64 holds every difference, else
    a float64 holding each rounded once. Any other number is a float64. A
    nullable column stays nullable (Int64 / Float64)."""
    # both sides are one aggregate of one column: nullable alike
    nullable = isinstance(a.dtype, pd.api.extensions.ExtensionDtype)
    if {a.dtype.kind, b.dtype.kind} <= {"b", "i", "u"}:
        exact = [
            None if pd.isna(x) or pd.isna(y) else int(x) - int(y)
            for x, y in zip(a.tolist(), b.tolist(), strict=True)
        ]
        whole = all(v is None or _INT64.min <= v <= _INT64.max for v in exact)
        kind = ("Int64" if whole else "Float64") if nullable else ("int64" if whole else "float64")
        return pd.Series(pd.array(exact, dtype=kind), index=a.index)
    wide = "Float64" if nullable else "float64"
    return a.astype(wide) - b.astype(wide)


def _diff(df: pd.DataFrame, t: Mapping[str, Any]) -> pd.DataFrame:
    by, items, groupby = t["diff"]["by"], t["aggregate"], list(t.get("groupby", []))
    need_columns(df, by)
    side = _comparable(df[by], by)  # a side as a predicate reads it: `equal`
    of = aggregate(df[_equals(side, t["diff"]["of"], f"diff on {by!r}: of")], items, groupby)
    minus = aggregate(
        df[_equals(side, t["diff"]["minus"], f"diff on {by!r}: minus")], items, groupby
    )
    # A group either side has is kept (#847/#848 PR 5 P42 row 32): a count or a
    # sum over no row is 0, any other aggregate of no row is missing.
    keys = groupby or [_ALL]
    if not groupby:
        of, minus = of.assign(**{_ALL: 0}), minus.assign(**{_ALL: 0})
    both = of.merge(minus, on=keys, how="outer", sort=True, suffixes=("", " minus"))
    for i in items:
        name = i["as"]
        a, b = both[name], both[f"{name} minus"]
        if i["op"] in ("count", "sum"):
            # the sides' common type: a count stays whole, a sum of 2.5 is not cut
            kind = pd.concat([of[name], minus[name]]).dtype
            a, b = a.fillna(0).astype(kind), b.fillna(0).astype(kind)
        both[name] = _minus(a, b)
    return both[[*groupby, *(i["as"] for i in items)]]


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
