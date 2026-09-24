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


def _js_number(x: float) -> str:
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
        return str(int(value))
    if isinstance(value, float | np.floating):
        f = float(value)
        return _js_number(f) if math.isfinite(f) else None
    return str(value)


def bitset(mask: pd.Series) -> str:
    bits = np.packbits(mask.to_numpy(dtype=bool), bitorder="little")
    return _b64(bits)


def _f64(values: np.ndarray) -> str:
    return _b64(np.ascontiguousarray(values, dtype="<f8"))


def encode_column(s: pd.Series, kind: str) -> dict[str, Any]:
    """`s` on the wire as `kind` (see the module doc)."""
    if kind == "f64":
        return {"kind": "f64", "data": _f64(pd.to_numeric(s, errors="coerce").to_numpy(float))}
    if kind == "time":
        # format="mixed": pandas otherwise infers ONE format from the first
        # value, and with errors="coerce" a second value written with more
        # precision (`…:56.789Z` after `…:00Z`) became a silent gap.
        stamps = pd.to_datetime(s, errors="coerce", utc=True, format="mixed")
        ms = pd.DatetimeIndex(stamps).asi8 / 1e6  # ns since the epoch → ms
        return {"kind": "time", "data": _f64(np.where(stamps.isna(), np.nan, ms))}
    if kind == "q8":
        return _q8(pd.to_numeric(s, errors="coerce").to_numpy(float))
    return _cat(s)


def _cat(s: pd.Series) -> dict[str, Any]:
    try:
        codes, uniques = pd.factorize(s, sort=True, use_na_sentinel=True)
    except TypeError:  # values with no order between them (a date among numbers)
        codes, uniques = pd.factorize(s, sort=False, use_na_sentinel=True)
    levels = [_json_scalar(v) for v in uniques]
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
