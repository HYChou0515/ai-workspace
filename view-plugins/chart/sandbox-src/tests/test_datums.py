"""#847/#848 PR 5 P26: a temporal datum is read on its axis's clock.

Since P14 a zoned time axis shows its column's zone, and a filter reads a
zone-less time there; a rule's zone-less datum was still read as UTC, so
`2026-03-01T12:00` sat at 20:00 on a Taipei axis. It is now a wall time in
the axis's zone, as a filter reads one — and, since a rule needs ONE place, a
wall time the zone had twice or never is refused by name.
"""

from __future__ import annotations

import datetime as dt

import pytest

from chart_view.datums import datum_errors, datum_instant, instant_ms
from chart_view.wire import zone_of


def _ms(*args: int, tz: dt.tzinfo = dt.UTC) -> float:
    return dt.datetime(*args, tzinfo=tz).timestamp() * 1000  # type: ignore[misc]


@pytest.mark.parametrize(
    ("name", "offset"),
    [("+08:00", 8 * 60), ("-05:30", -(5 * 60 + 30)), ("UTC", 0)],
)
def test_zone_of_reads_a_fixed_offset(name, offset):
    assert zone_of(name).utcoffset(None) == dt.timedelta(minutes=offset)


def test_zone_of_reads_an_iana_name():
    assert zone_of("Asia/Taipei").utcoffset(dt.datetime(2026, 3, 1)) == dt.timedelta(hours=8)


def test_a_zoneless_datum_is_a_wall_time_on_a_zoned_axis():
    assert datum_instant("2026-03-01T12:00", "Asia/Taipei") == _ms(2026, 3, 1, 4)
    assert datum_instant("2026-03-01", "Asia/Taipei") == _ms(2026, 2, 28, 16)
    assert datum_instant("2026-03-01T12:00", "+05:45") == _ms(2026, 3, 1, 6, 15)


def test_a_datum_with_a_zone_or_a_number_is_that_instant_on_any_axis():
    assert datum_instant("2026-03-01T12:00:00+00:00", "Asia/Taipei") == _ms(2026, 3, 1, 12)
    assert datum_instant("2026-03-01T12:00Z", "Asia/Taipei") == _ms(2026, 3, 1, 12)
    assert datum_instant(1772366400000, "Asia/Taipei") == 1772366400000


def test_a_zoneless_datum_is_utc_on_a_zoneless_axis():
    assert datum_instant("2026-03-01T12:00", None) == _ms(2026, 3, 1, 12)


def test_a_wall_time_the_zone_skipped_is_refused_by_name():
    # New York sprang from 02:00 to 03:00 on 2026-03-08: 02:30 never happened
    why = datum_instant("2026-03-08T02:30", "America/New_York")
    assert why == (
        "is a time America/New_York never had (its clocks went forward past it) "
        "— write it with its offset, as 2026-03-08T02:30:00-05:00"
    )
    assert datum_instant("2026-03-08T03:00", "America/New_York") == _ms(2026, 3, 8, 7)


def test_a_wall_time_the_zone_had_twice_is_refused_by_name():
    # New York fell back from 02:00 to 01:00 on 2026-11-01: 01:30 happened twice
    why = datum_instant("2026-11-01T01:30", "America/New_York")
    assert why == (
        "is a time America/New_York had twice (its clocks went back) "
        "— write it with its offset, as 2026-11-01T01:30:00-04:00 or 2026-11-01T01:30:00-05:00"
    )
    # the offsets named keep the datum's own milliseconds
    assert datum_instant("2026-11-01T01:30:00.250", "America/New_York").endswith(
        "as 2026-11-01T01:30:00.250-04:00 or 2026-11-01T01:30:00.250-05:00"
    )


def test_a_wall_time_whose_instant_falls_off_the_calendar_at_utc_is_still_placed():
    # 0001-01-01 00:00 at +05:00 is 5 hours before the first instant Python
    # holds at UTC; the renderer places it, as it places the same text zoned
    assert datum_instant("0001-01-01", "+05:00") == instant_ms("0001-01-01T00:00+05:00")
    assert datum_instant("9999-12-31T23:00", "-05:00") == instant_ms("9999-12-31T23:00-05:00")


def _chart(datum, layers=None):
    line = {
        "mark": "line",
        "encoding": {
            "x": {"field": "t", "type": "temporal"},
            "y": {"field": "v", "type": "quantitative"},
        },
    }
    rule = {"mark": "rule", "encoding": {"x": {"datum": datum}}}
    return {"view": "chart", "source": "a.csv", "layer": [*(layers or [line]), rule]}


def _answer(*zones):
    """An answer whose layers send `t` as time in these zones (None: none)."""
    cols = [{"t": {"kind": "time", "data": "", **({"zone": z} if z else {})}} for z in zones]
    return lambda: {"layers": [{"columns": c} for c in [*cols, {}]]}


def test_a_rule_reads_a_zoneless_datum_in_its_axis_zone():
    # the axis's zone is the first layer's that sends the field as time, as
    # the renderer's clock takes it (option.ts channelClock)
    assert datum_errors(_chart("2026-03-08T02:30"), _answer("America/New_York")) == [
        "x: datum '2026-03-08T02:30' is a time America/New_York never had (its clocks went "
        "forward past it) — write it with its offset, as 2026-03-08T02:30:00-05:00"
    ]
    assert datum_errors(_chart("2026-03-08T02:30"), _answer(None)) == []
    cat = {
        "mark": "bar",
        "encoding": {
            "x": {"field": "t", "type": "nominal"},
            "y": {"field": "v", "type": "quantitative"},
        },
    }
    line = _chart("x")["layer"][0]
    two = lambda: {  # noqa: E731
        "layers": [
            {"columns": {"t": {"kind": "cat", "levels": [], "width": 1, "codes": ""}}},
            {"columns": {"t": {"kind": "time", "data": "", "zone": "America/New_York"}}},
            {"columns": {}},
        ]
    }
    assert datum_errors(_chart("2026-11-01T01:30", [line, cat]), two)[0].startswith(
        "x: datum '2026-11-01T01:30' is a time America/New_York had twice"
    )


def test_a_rule_builds_no_answer_for_a_datum_that_names_its_instant():
    def never():
        raise AssertionError("the answer was built")

    assert datum_errors(_chart("2026-03-08T02:30:00-05:00"), never) == []
    assert datum_errors(_chart(1772366400000), never) == []
    assert datum_errors(_chart("2024-02-30"), never)[0].startswith(
        "x: datum '2024-02-30' is not a date"
    )


def test_a_datum_that_is_no_date_says_how_to_write_one():
    assert datum_instant("2024-02-30", "Asia/Taipei") == (
        "is not a date the chart can place — write it as 2024-03-01, "
        "2024-03-01T12:00, or with a zone as 2024-03-01T12:00:00+08:00"
    )
    assert datum_instant("2024-02-30", None) == datum_instant("2024-02-30", "Asia/Taipei")
