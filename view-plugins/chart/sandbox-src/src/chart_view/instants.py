"""A date a spec writes, or a date field left as text: the one reading of it.

A spec's text is a date only in the schema's `$defs.instant` forms — the forms
a temporal datum takes (`datums.instant_ms` is held to the renderer, and
`parse_instant` to `instant_ms`, by wire-corpus/instants.json) — or, for a
filter, in the form a marking writes (`wire.canon`: up to nine fraction digits;
`parse_date_text`); and a number is epoch ms. pandas' own parser read "now" as
the wall clock and "March" as 0001-03-01.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import zoneinfo
from decimal import Decimal
from typing import Any, cast

import pandas as pd
from pandas.errors import OutOfBoundsDatetime

from chart_view.spec import spec_schema

_DATUM = spec_schema()["$defs"]["instant"]["pattern"]
_MILLIS = "([0-9]{1,3})"  # the fraction group: a datum is written to the millisecond
assert _DATUM.count(_MILLIS) == 1
INSTANT = re.compile(_DATUM)
# A marking writes a timestamp as pandas prints it: to the micro- or nanosecond.
DATE_TEXT = re.compile(_DATUM.replace(_MILLIS, "([0-9]{1,9})"))
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


def parse_instant(text: str) -> dt.datetime | None:
    """A datum's `text` as a datetime — aware when written with a zone, naive
    when not — or None: text in no `$defs.instant` form, or a day the calendar
    lacks."""
    return _read(INSTANT.fullmatch(text))


def parse_date_text(text: str) -> dt.datetime | None:
    """A filter's or a date column's `text`, as `parse_instant` reads a datum,
    and also as a marking writes it (a `pd.Timestamp` when finer than a
    microsecond)."""
    return _read(DATE_TEXT.fullmatch(text))


def _read(m: re.Match[str] | None) -> dt.datetime | None:
    if m is None:
        return None
    # groups by number, as the pattern's $comment lists them
    y, _, mo, d, h, mi, s, frac, zone, zh, zm = m.groups()
    digits = (frac or "").ljust(9, "0")
    try:
        at: dt.datetime = dt.datetime(
            int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0), int(digits[:6])
        )
        if digits[6:] != "000":
            stamp = pd.Timestamp(at).as_unit("ns").replace(nanosecond=int(digits[6:]))
            at = cast(dt.datetime, stamp)  # a datetime, never NaT
    except (ValueError, OverflowError):  # 2024-02-30; a nanosecond past 1677-2262
        return None
    if zone is None:
        return at
    if zone == "Z":
        return at.replace(tzinfo=dt.UTC)
    offset = dt.timedelta(hours=int(zh), minutes=int(zm))
    return at.replace(tzinfo=dt.timezone(-offset if zone[0] == "-" else offset))


def instant_of_number(ms: Any) -> dt.datetime | None:
    """Epoch ms as a UTC instant, as a temporal datum reads a number — to the
    nanosecond, read from the number as written (a timedelta rounds to the
    microsecond, and `ms * 1e6` is off by up to 128 ns at today's epoch). None
    for a bool, a non-finite number, or one past the calendar."""
    if isinstance(ms, bool) or not isinstance(ms, int | float) or not math.isfinite(ms):
        return None
    ns = int(Decimal(repr(ms)) * 1_000_000)
    try:
        return cast(dt.datetime, pd.Timestamp(ns, unit="ns", tz="UTC"))  # never NaT
    except (OutOfBoundsDatetime, OverflowError):
        pass
    try:  # past 1677-2262 a float's spacing is ≥ 1.9 µs: no digit finer than a µs
        return _EPOCH + dt.timedelta(microseconds=ns // 1000)
    except OverflowError:
        return None


def with_folds(zone: dt.tzinfo) -> dt.tzinfo:
    """`zone` as a tzinfo that reads a wall time with `fold`: a pytz zone (what
    pandas hands a parquet or string zone) as the zoneinfo zone of its name.

    pandas reads no time before 1677 in a pytz table zone, and a pytz array outside
    nanoseconds crashes pandas' astype(object) (map, isin, `canon`)."""
    name = getattr(zone, "zone", None)  # pytz's own attribute; a fixed offset has None
    return zoneinfo.ZoneInfo(name) if isinstance(name, str) else zone


def local_instants(wall: dt.datetime, zone: dt.tzinfo) -> list[dt.datetime]:
    """The UTC instants a zone-less wall time names in `zone` — the zone a column
    carries, read as that column shows its own times: two where the clocks went
    back, none where they sprang ahead. OverflowError when one falls off the
    calendar at UTC."""
    out: list[dt.datetime] = []
    if hasattr(zone, "localize"):  # pytz: its own table (New York's LMT is -4:56 there)
        pz: Any = zone  # pytz ships no types
        for is_dst in (True, False):  # the earlier instant first, as fold 0 is
            local = pz.localize(wall, is_dst=is_dst)
            at = local.astimezone(dt.UTC)
            if pz.normalize(local).replace(tzinfo=None) == wall and at not in out:
                out.append(at)
        return out
    for fold in (0, 1):
        at = wall.replace(tzinfo=zone, fold=fold).astimezone(dt.UTC)
        if at.astimezone(zone).replace(tzinfo=None) == wall and at not in out:
            out.append(at)
    return out
