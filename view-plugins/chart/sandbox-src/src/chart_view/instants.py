"""A date a spec writes, or a date field left as text: the one reading of it.

A spec's text is a date only in the schema's `$defs.instant` forms — the forms
a temporal datum takes (`datums.instant_ms` is held to the renderer, and this
reading to `instant_ms`, by wire-corpus/instants.json) — and a number is epoch
ms. pandas' own parser read "now" as the wall clock and "March" as 0001-03-01.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import zoneinfo
from typing import Any

from chart_view.spec import spec_schema

_INSTANT = re.compile(spec_schema()["$defs"]["instant"]["pattern"])
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


def parse_instant(text: str) -> dt.datetime | None:
    """`text` as a datetime — aware when written with a zone, naive when not —
    or None: text in no `$defs.instant` form, or a day the calendar lacks."""
    m = _INSTANT.fullmatch(text)
    if m is None:
        return None
    # groups by number, as the pattern's $comment lists them
    y, _, mo, d, h, mi, s, frac, zone, zh, zm = m.groups()
    micro = int((frac or "0").ljust(6, "0"))
    try:
        at = dt.datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0), micro)
    except ValueError:  # 2024-02-30
        return None
    if zone is None:
        return at
    if zone == "Z":
        return at.replace(tzinfo=dt.UTC)
    offset = dt.timedelta(hours=int(zh), minutes=int(zm))
    return at.replace(tzinfo=dt.timezone(-offset if zone[0] == "-" else offset))


def instant_of_number(ms: Any) -> dt.datetime | None:
    """Epoch ms as a UTC instant, as a temporal datum reads a number; None for a
    bool, a non-finite number, or one past the calendar."""
    if isinstance(ms, bool) or not isinstance(ms, int | float) or not math.isfinite(ms):
        return None
    try:
        return _EPOCH + dt.timedelta(milliseconds=ms)
    except OverflowError:
        return None


def with_folds(zone: dt.tzinfo) -> dt.tzinfo:
    """`zone` as a tzinfo that reads a wall time with `fold`: a pytz zone (what
    pandas hands a parquet or string zone) as the zoneinfo zone of its name.

    pytz reads no time before 1677 in a table zone, and a pytz array outside
    nanoseconds crashes pandas' astype(object) (map, isin, `canon`)."""
    name = getattr(zone, "zone", None)  # pytz's own attribute; a fixed offset has None
    return zoneinfo.ZoneInfo(name) if isinstance(name, str) else zone


def local_instants(wall: dt.datetime, zone: dt.tzinfo) -> list[dt.datetime]:
    """The UTC instants a zone-less wall time names in `zone`: two where the
    clocks went back, none where they sprang ahead. OverflowError when one
    falls off the calendar at UTC."""
    zone = with_folds(zone)
    out: list[dt.datetime] = []
    for fold in (0, 1):
        at = wall.replace(tzinfo=zone, fold=fold).astimezone(dt.UTC)
        if at.astimezone(zone).replace(tzinfo=None) == wall and at not in out:
            out.append(at)
    return out
