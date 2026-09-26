"""A category column's wire (#847/#848 P33): a column that can hold no list,
mapping or array reaches factorize without a Python call per row, and every
column's wire is the one ``_cat`` wrote before that (the oracle below is its
code as it stood, copied verbatim, so the two are never kept alike by hand).

The per-row map cost ~16 of 28 s on a 200 x 50 000 facet gallery with ordinal
x and y (10M rows): ~20M calls that return their argument unchanged."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import numpy as np
import pandas as pd
import pytest

from chart_view import wire
from chart_view.wire import _WIDTHS, _as_marking, _b64, _json_scalar, encode_column


def _oracle_cat(s: pd.Series) -> dict[str, Any]:
    """``wire._cat`` before P33, verbatim."""
    integral = pd.api.types.is_integer_dtype(s.dtype)
    s = s.map(_as_marking)
    try:
        codes, uniques = pd.factorize(s, sort=True, use_na_sentinel=True)
    except TypeError:
        codes, uniques = pd.factorize(s, sort=False, use_na_sentinel=True)
    levels = [_json_scalar(v) for v in uniques]
    if integral:
        levels = [int(v) if isinstance(v, float) else v for v in levels]
    width, dtype = next((w, d) for w, d in _WIDTHS if len(levels) < 2 ** (8 * w) - 1)
    missing = 2 ** (8 * width) - 1
    out = np.where(codes < 0, missing, codes).astype(dtype)
    return {"kind": "cat", "levels": levels, "width": width, "codes": _b64(out)}


_TZ = "Asia/Taipei"
_rng = np.random.default_rng(7)

CORPUS: dict[str, pd.Series] = {
    # numbers
    "int64": pd.Series([3, 1, 2, 1, -7]),
    "int8": pd.Series([3, 1, 2], dtype="int8"),
    "uint64-past-2**63": pd.Series([2**63 + 5, 1, 2**64 - 1], dtype="uint64"),
    "int64-past-2**53": pd.Series([2**53 + 1, 2**53 + 2, 5]),
    "int-300-levels": pd.Series(_rng.integers(0, 300, 2000)),
    "int-70000-levels": pd.Series(np.arange(70000)[::-1]),
    "Int64-with-null": pd.Series([3, None, 1, 3], dtype="Int64"),
    "Int64-past-2**53-with-null": pd.Series([2**53 + 1, 2**53 + 2, None], dtype="Int64"),
    "Int64-no-null": pd.Series([3, 1], dtype="Int64"),
    "float-nan-inf-zeros": pd.Series([0.5, np.nan, -0.0, 0.0, np.inf, -np.inf, 1e21, 1e-7]),
    "float32": pd.Series([0.1, 0.5, np.nan], dtype="float32"),
    "Float64-with-null": pd.Series([0.5, None, 1.5], dtype="Float64"),
    "complex": pd.Series([1 + 2j, 3j, 1 + 2j]),
    "bool": pd.Series([True, False, True]),
    "boolean-with-null": pd.Series([True, None, False], dtype="boolean"),
    # instants and durations
    "datetime-naive": pd.Series(pd.to_datetime(["2020-01-02", None, "2020-01-01", "2020-01-02"])),
    "datetime-seconds": pd.Series(pd.to_datetime(["2020-01-02", "2020-01-01"])).astype(
        "datetime64[s]"
    ),
    "datetime-seconds-past-ns-range": pd.Series(
        np.array(["3000-01-01", "1500-06-01", "NaT"], dtype="datetime64[s]")
    ),
    "datetime-zoned": pd.Series(
        pd.to_datetime(["2020-01-02 03:00", None, "2020-01-01 00:00"])
    ).dt.tz_localize(_TZ),
    "datetime-zoned-us": pd.Series(
        pd.to_datetime(["2020-01-02 03:00:00.0", "2020-01-01 00:00:00.5"])
    )
    .dt.tz_localize("UTC")
    .astype("datetime64[us, UTC]"),
    "datetime-zoned-past-ns-range": pd.Series(
        np.array(["3000-01-01", "1500-06-01", "NaT"], dtype="datetime64[s]")
    ).dt.tz_localize("UTC"),
    "timedelta": pd.Series(pd.to_timedelta(["1h", None, "2s"])),
    "timedelta-days": pd.Series(np.array([10**6, 5, "NaT"], dtype="timedelta64[D]")),
    "period": pd.Series(pd.period_range("2020-01", periods=3, freq="M")),
    # text
    "text": pd.Series(["b", "a", None, "b"]),
    "string": pd.Series(["b", "a", None], dtype="string"),
    "string-pyarrow": pd.Series(["b", "a", None], dtype="string[pyarrow]"),
    "text-300-levels": pd.Series([f"v{i}" for i in range(300)], dtype=object),
    # object columns of mixed kinds
    "object-ints": pd.Series([3, 1, 2], dtype=object),
    "object-ints-and-floats": pd.Series([1, 2.5, 3], dtype=object),
    "object-numbers-and-text": pd.Series([1, "a", 2.5, None, "7", 7], dtype=object),
    "object-a-date-among-numbers": pd.Series([1, dt.date(2020, 1, 1), 2], dtype=object),
    "object-a-stamp-among-numbers": pd.Series(
        [pd.Timestamp("2024-01-01"), 1.5, pd.Timestamp("2024-01-01")], dtype=object
    ),
    "object-bools-and-null": pd.Series([True, None, False], dtype=object),
    "object-stamps": pd.Series([pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-01")]).astype(
        object
    ),
    "object-inf": pd.Series([np.inf, 1.0], dtype=object),
    "object-containers": pd.Series(
        [[1, 2], {"b": 1, "a": 2}, np.array([1, 2]), None, 3, [1, 2], (1, 2), {3, 1}]
    ),
    "object-arrays-only": pd.Series([np.array([1.5, 2.0]), np.array([1.5, 2.0]), np.array([])]),
    # empty and missing
    "empty-float": pd.Series([], dtype=float),
    "empty-object": pd.Series([], dtype=object),
    "empty-int": pd.Series([], dtype="int64"),
    "all-null-object": pd.Series([None, np.nan], dtype=object),
    "all-null-float": pd.Series([np.nan, np.nan]),
    "all-NaT": pd.Series(pd.to_datetime([None, None])),
    # categories
    "category-text": pd.Series(["b", "a", "b", None], dtype="category"),
    "category-ordered": pd.Series(
        pd.Categorical(["b", "a", "b"], categories=["b", "a"], ordered=True)
    ),
    "category-ints": pd.Series([3, 1, None], dtype="category"),
}


@pytest.mark.parametrize("name", list(CORPUS))
def test_a_category_wire_is_the_one_written_before(name: str):
    s = CORPUS[name]
    before = s.copy()
    got, want = encode_column(s, "cat"), _oracle_cat(s)
    # as JSON text, and type for type: == holds 1 == 1.0 == True, the wire does not
    assert json.dumps(got) == json.dumps(want)
    assert [type(v) for v in got["levels"]] == [type(v) for v in want["levels"]]
    pd.testing.assert_series_equal(s, before)  # the caller's column is left as it was


@pytest.mark.parametrize(
    "name",
    [
        "int64",
        "uint64-past-2**63",
        "float-nan-inf-zeros",
        "complex",
        "bool",
        "datetime-naive",
        "datetime-zoned",
        "timedelta",
    ],
)
def test_a_column_that_cannot_hold_a_container_is_not_mapped_per_row(name, monkeypatch):
    calls = []

    def spy(v):
        calls.append(v)
        return _as_marking(v)

    monkeypatch.setattr(wire, "_as_marking", spy)
    encode_column(CORPUS[name], "cat")
    assert calls == []


def test_an_object_column_is_still_read_a_cell_at_a_time(monkeypatch):
    """The positive control of the test above: its spy does see the cells."""
    calls = []

    def spy(v):
        calls.append(v)
        return _as_marking(v)

    monkeypatch.setattr(wire, "_as_marking", spy)
    wire_ = encode_column(CORPUS["object-containers"], "cat")
    assert len(calls) == len(CORPUS["object-containers"])
    assert "[1, 2]" in wire_["levels"]
