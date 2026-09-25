"""`chart_view.instants`: the one reading of a date a spec writes or a date field
left as text (round 14)."""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from chart_view.datums import instant_ms
from chart_view.instants import instant_of_number, local_instants, parse_instant

_CORPUS = json.loads(
    (Path(__file__).resolve().parents[2] / "wire-corpus" / "instants.json").read_text()
)["cases"]


def _ms(at: dt.datetime | None) -> float | None:
    if at is None:
        return None
    try:
        aware = at if at.tzinfo else at.replace(tzinfo=dt.UTC)
        epoch = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
        return (aware - epoch) // dt.timedelta(microseconds=1) / 1000  # exact, no float drift
    except OverflowError:
        return None


@pytest.mark.parametrize("text", [c["text"] for c in _CORPUS])
def test_a_spec_date_reads_as_the_datum_reads_it(text):
    # Parity: `instant_ms` (what the renderer is held to) is the oracle.
    assert _ms(parse_instant(text)) == instant_ms(text)


def test_a_zone_less_date_stays_zone_less_and_a_zoned_one_keeps_its_offset():
    assert parse_instant("2024-03-01T12:00:00.5") == dt.datetime(2024, 3, 1, 12, 0, 0, 500000)
    zoned = parse_instant("2024-03-01T12:00-0530")
    assert zoned is not None and zoned.utcoffset() == -dt.timedelta(hours=5, minutes=30)


@pytest.mark.parametrize("text", ["now", "today", "March", "3pm", "", "NaT", "2024-02-30"])
def test_text_that_is_no_spec_date_reads_as_none(text):
    # pd.Timestamp read "now" as the wall clock and "March" as 0001-03-01.
    assert parse_instant(text) is None


@pytest.mark.parametrize(
    ("value", "expect"),
    [
        (1730611800000, dt.datetime(2024, 11, 3, 5, 30, tzinfo=dt.UTC)),
        (0.5, dt.datetime(1970, 1, 1, 0, 0, 0, 500, tzinfo=dt.UTC)),
        (True, None),
        (math.inf, None),
        (math.nan, None),
        (1e20, None),  # past 9999
    ],
)
def test_a_number_is_epoch_milliseconds_an_instant(value, expect):
    assert instant_of_number(value) == expect


def _utc(*a: int) -> dt.datetime:
    return dt.datetime(*a, tzinfo=dt.UTC)


@pytest.mark.parametrize("zone", ["pytz", "zoneinfo"])
@pytest.mark.parametrize(
    ("wall", "expect"),
    [
        # the clocks went back: EDT, then EST
        (dt.datetime(2024, 11, 3, 1, 30), [_utc(2024, 11, 3, 5, 30), _utc(2024, 11, 3, 6, 30)]),
        (dt.datetime(2024, 3, 10, 2, 30), []),  # they sprang ahead
        (dt.datetime(2024, 1, 1), [_utc(2024, 1, 1, 5)]),  # once: one instant, not two
        # before any zone table: New York's local mean time, -4:56:02
        (dt.datetime(1600, 1, 1), [_utc(1600, 1, 1, 4, 56, 2)]),
    ],
    ids=["overlap", "gap", "ordinary", "before-the-tables"],
)
def test_a_wall_time_names_the_instants_the_zone_gave_it(zone, wall, expect):
    ny = pd.Series(pd.to_datetime(["2024-01-01"])).dt.tz_localize("America/New_York").dt.tz
    if zone == "zoneinfo":
        import zoneinfo

        ny = zoneinfo.ZoneInfo("America/New_York")
    assert local_instants(wall, ny) == expect


def test_a_fixed_offset_zone_names_one_instant():
    plus8 = dt.timezone(dt.timedelta(hours=8))
    assert local_instants(dt.datetime(2024, 1, 1, 8), plus8) == [_utc(2024, 1, 1)]
