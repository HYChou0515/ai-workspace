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
import math
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


def epoch_ms(s: pd.Series) -> np.ndarray:
    """A temporal column as epoch milliseconds (NaN = missing).

    Numbers ARE epoch milliseconds, as in Vega-Lite (pandas would read them as
    nanoseconds: 1700000000000 became 1970-01-01T00:28). Text is parsed with
    format="mixed": pandas otherwise infers ONE format from the first value,
    and with errors="coerce" a later, more precise one became a silent gap."""
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return pd.to_numeric(s, errors="coerce").to_numpy(float)
    # Text (or a mix, as an entity field or a hand-made CSV can hold): each
    # value its own way — a number is ms, a date is parsed, and digits that do
    # not parse as a date are ms too (a CSV holds numbers as text).
    stamps = pd.to_datetime(
        s.map(lambda v: None if _is_number(v) else v), errors="coerce", utc=True, format="mixed"
    )
    # ns → µs as integers first: ns / 1e6 in floats read .250 s as .2499998.
    ms = np.where(stamps.isna(), np.nan, (pd.DatetimeIndex(stamps).asi8 // 1_000) / 1_000)
    as_number = pd.to_numeric(
        s.map(
            lambda v: v if _is_number(v) or (isinstance(v, str) and v.strip().isdigit()) else None
        ),
        errors="coerce",
    ).to_numpy(float)
    return np.where(np.isnan(ms), as_number, ms)


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float | np.integer | np.floating) and not isinstance(v, bool)


def _cat(s: pd.Series) -> dict[str, Any]:
    integral = pd.api.types.is_integer_dtype(s.dtype)  # before the map below makes it object
    # A list or a mapping (an entity field can hold one) has no hash to group
    # by; it becomes its marking string.
    s = s.map(lambda v: canon(v) if isinstance(v, list | dict | set | tuple) else v)
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
