"""The bytes `query` answers in.

Columns travel column-wise and binary, base64-encoded (plan Q11: JSON floats
cost more to parse than to send):

- ``f64``  — ``{"kind": "f64", "data": b64}``: little-endian float64, NaN = missing.
- ``time`` — the same, as epoch milliseconds (what ECharts' time axis takes).
- ``cat``  — ``{"kind": "cat", "levels": [...], "width": 1|2|4, "codes": b64}``:
  little-endian unsigned codes into ``levels``; the width's largest value
  (255 / 65535 / 2**32-1) is a missing value.
- ``q8``   — ``{"kind": "q8", "min", "max", "codes": b64}``: a value quantized to
  level 0..254 over [min, max]; 255 is missing. The grid raster paints these
  codes directly, so "same cells → same pixels" holds for every host of it.

A highlight is a bitset: row i is bit ``i % 8`` (LSB first) of byte ``i // 8``.

`canon` is how a value becomes the opaque string a marking compares (Q6). It is
JavaScript's ``String(v)``, because the other writer of markings is the browser.
`wire-corpus/` pins all of this against the renderer's decoder.
"""

from __future__ import annotations

import base64
import datetime as dt
import math
import re
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

_WIDTHS = ((1, "<u1"), (2, "<u2"), (4, "<u4"))


def _b64(array: np.ndarray) -> str:
    return base64.b64encode(array.tobytes()).decode("ascii")


def js_number(x: float) -> str:
    """ECMAScript Number::toString for a finite float (shortest round-trip digits)."""
    if x == 0:
        return "0"
    sign, digits_t, exponent = Decimal(repr(abs(x))).normalize().as_tuple()
    digits = "".join(map(str, digits_t))
    k = len(digits)
    n = k + int(exponent)  # the decimal point sits after the n-th digit
    minus = "-" if x < 0 else ""
    if k <= n <= 21:
        return minus + digits + "0" * (n - k)
    if 0 < n <= 21:
        return minus + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return minus + "0." + "0" * (-n) + digits
    e = n - 1
    mantissa = digits[0] + ("." + digits[1:] if k > 1 else "")
    return f"{minus}{mantissa}e{'+' if e > 0 else '-'}{abs(e)}"


def canon(value: Any) -> str | None:
    """The marking string for `value`, or None when it has none (missing, ±inf)."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, bool | np.bool_):
        return "true" if value else "false"
    if isinstance(value, int | np.integer):
        n = int(value)
        # Past 2**53 the browser holds the nearest double, and prints that.
        return str(n) if abs(n) <= 2**53 else js_number(float(n))
    if isinstance(value, float | np.floating):
        f = float(value)
        return js_number(f) if math.isfinite(f) else None
    return str(value)


def bitset(mask: pd.Series) -> str:
    bits = np.packbits(mask.to_numpy(dtype=bool), bitorder="little")
    return _b64(bits)


def _f64(values: np.ndarray) -> str:
    out = np.array(values, dtype="<f8")  # a copy: the caller's array stays as it was
    # ±inf is missing on the wire, as NaN is: no axis or colour scale can hold
    # it, and validate's summary already leaves it out.
    out[~np.isfinite(out)] = np.nan
    return _b64(out)


def encode_column(s: pd.Series, kind: str) -> dict[str, Any]:
    """`s` on the wire as `kind` (see the module doc)."""
    if kind == "f64":
        return {"kind": "f64", "data": _f64(pd.to_numeric(s, errors="coerce").to_numpy(float))}
    if kind == "time":
        return {"kind": "time", "data": _f64(epoch_ms(s))}
    if kind == "q8":
        return _q8(pd.to_numeric(s, errors="coerce").to_numpy(float))
    return _cat(s)


def decode_column(wire: dict[str, Any]) -> list[Any]:
    """The values the renderer's `decodeColumn` reads from `wire` (None =
    missing), held to wire-corpus/ as the renderer is. What validate judges a
    rule's datum against: the rows as the chart receives them, after binning
    and each layer's own kind, not as pandas holds them."""
    kind = wire["kind"]
    if kind in ("f64", "time"):
        data = np.frombuffer(base64.b64decode(wire["data"]), dtype="<f8")
        return [None if math.isnan(v) else float(v) for v in data]
    if kind == "cat":
        width = wire["width"]
        codes = np.frombuffer(base64.b64decode(wire["codes"]), dtype=dict(_WIDTHS)[width])
        missing = 2 ** (8 * width) - 1
        return [None if c == missing else wire["levels"][c] for c in codes.tolist()]
    codes = np.frombuffer(base64.b64decode(wire["codes"]), dtype="<u1")
    span = wire["max"] - wire["min"]
    return [None if c == 255 else wire["min"] + (c / 254) * span for c in codes.tolist()]


def decode_distinct(wire: dict[str, Any]) -> list[Any]:
    """The distinct non-missing values `decode_column` reads, without a list
    per row: what a category axis's labels are made of."""
    kind = wire["kind"]
    if kind in ("f64", "time"):
        data = np.frombuffer(base64.b64decode(wire["data"]), dtype="<f8")
        return np.unique(data[~np.isnan(data)]).tolist()
    width = wire.get("width", 1)
    codes = np.frombuffer(base64.b64decode(wire["codes"]), dtype=dict(_WIDTHS)[width])
    present = np.unique(codes)
    if kind == "cat":
        missing = 2 ** (8 * width) - 1
        return [wire["levels"][c] for c in present.tolist() if c != missing]
    span = wire["max"] - wire["min"]
    return [wire["min"] + (c / 254) * span for c in present.tolist() if c != 255]


def epoch_ms(s: pd.Series) -> np.ndarray:
    """A temporal column as epoch milliseconds (NaN = missing).

    A number — or text that is one — IS epoch milliseconds, as in Vega-Lite
    (pandas would read a number as nanoseconds: 1700000000000 became
    1970-01-01T00:28). Other text is a date,
    parsed with format="mixed": pandas otherwise infers ONE format from the
    first value, and with errors="coerce" a later, more precise one became a
    silent gap. Text with no zone is UTC."""
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return pd.to_numeric(s, errors="coerce").to_numpy(float)
    if isinstance(s.dtype, pd.DatetimeTZDtype):
        # As a column, keeping its unit: the object path below reads the same
        # instants value by value, ~500x slower (6.8 s per million rows).
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    if pd.api.types.is_datetime64_dtype(s.dtype):
        # Straight from the column's own unit: a `datetime64[s]` column holds
        # 9999-12-31, which a detour through nanoseconds loses.
        arr = s.to_numpy()
        return np.where(np.isnat(arr), np.nan, arr.astype("datetime64[us]").astype("int64") / 1_000)
    number = s.map(_as_number).to_numpy(float)
    dates = s.map(lambda v: None if _is_number(v) or _NUMBER.match(str(v)) else v)
    stamps = pd.to_datetime(dates, errors="coerce", utc=True, format="mixed")
    # ns → µs as integers first: ns / 1e6 in floats read .250 s as .2499998.
    ms = np.where(stamps.isna(), np.nan, (pd.DatetimeIndex(stamps).asi8 // 1_000) / 1_000)
    # pandas holds nanoseconds, so only 1677–2262: past that (an open-ended
    # "valid to 9999-12-31"), the standard library reads the date.
    far = np.isnan(ms) & dates.notna().to_numpy()
    ms[far] = [_stdlib_ms(v) for v in dates[far]]
    return np.where(np.isnan(number), ms, number)


# A number written as text: digits, a sign, a decimal point — no `inf`, no `1_000`.
_NUMBER = re.compile(r"\s*[+-]?(?:\d+\.?\d*|\.\d+)\s*$")
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


def _is_number(v: Any) -> bool:
    # Decimal: a parquet decimal column reads as object Decimals.
    return isinstance(v, int | float | Decimal | np.integer | np.floating) and not isinstance(
        v, bool
    )


def _as_number(v: Any) -> float:
    if _is_number(v):
        return float(v)
    return float(v) if isinstance(v, str) and _NUMBER.match(v) else math.nan


def _stdlib_ms(v: Any) -> float:
    if isinstance(v, np.datetime64):
        return math.nan if np.isnat(v) else int(v.astype("datetime64[us]").astype("int64")) / 1_000
    if isinstance(v, str):
        try:  # fromisoformat takes no slashes; pandas read 2024/03/01 as a date
            v = dt.datetime.fromisoformat(v.strip().replace("/", "-"))
        except ValueError:  # not a date at all ("soon", "2024-02-30")
            return math.nan
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        v = dt.datetime(v.year, v.month, v.day)
    if not isinstance(v, dt.datetime):
        return math.nan
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.UTC)
    return (v - _EPOCH) / dt.timedelta(milliseconds=1)


def _cat(s: pd.Series) -> dict[str, Any]:
    integral = pd.api.types.is_integer_dtype(s.dtype)  # before the map below makes it object
    # A list or a mapping (an entity field, or a parquet list column read as
    # numpy arrays) has no hash to group by; it becomes its marking string.
    s = s.map(lambda v: canon(v) if isinstance(v, list | dict | set | tuple | np.ndarray) else v)
    try:
        codes, uniques = pd.factorize(s, sort=True, use_na_sentinel=True)
    except TypeError:  # values with no order between them (a date among numbers)
        codes, uniques = pd.factorize(s, sort=False, use_na_sentinel=True)
    levels = [_json_scalar(v) for v in uniques]
    if integral:
        # A nullable integer column (Int64 — a parquet int with a null) hands
        # factorize its values as floats; they are integers, and travel so.
        levels = [int(v) if isinstance(v, float) else v for v in levels]
    width, dtype = next((w, d) for w, d in _WIDTHS if len(levels) < 2 ** (8 * w) - 1)
    missing = 2 ** (8 * width) - 1
    out = np.where(codes < 0, missing, codes).astype(dtype)
    return {"kind": "cat", "levels": levels, "width": width, "codes": _b64(out)}


def _json_scalar(v: Any) -> Any:
    """A level as JSON can carry it: str / int / finite float / bool as they
    are, anything else (a timestamp, say) as its marking string. (Iterating
    factorize's Index already yields Python scalars, not numpy ones.)"""
    if isinstance(v, str | bool | int) or (isinstance(v, float) and math.isfinite(v)):
        return v
    return canon(v)


def _q8(values: np.ndarray) -> dict[str, Any]:
    finite = values[np.isfinite(values)]
    low = float(finite.min()) if finite.size else 0.0
    high = float(finite.max()) if finite.size else 0.0
    span = high - low
    scaled = np.zeros_like(values) if span == 0 else (values - low) / span * 254
    codes = np.where(np.isfinite(values), np.rint(np.nan_to_num(scaled)), 255).astype("<u1")
    return {"kind": "q8", "min": low, "max": high, "codes": _b64(codes)}
