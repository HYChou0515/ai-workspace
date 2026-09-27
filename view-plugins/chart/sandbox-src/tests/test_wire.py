"""The wire format `query` answers in, pinned by `wire-corpus/`.

Each corpus file is `{"kind", "values", "wire"}`: this test holds the encoder to
`values → wire`, and the renderer's `wire.test.ts` holds its decoder to
`wire → values`, so the two halves meet on the file rather than on each other.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import math
import zoneinfo
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import pytz

from chart_view.wire import bitset, canon, encode_column

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus"
DATUM_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "write_datum_corpus.py"
# A column case is a file with a `wire` answer; the corpus holds other tables too.
FILES = sorted(f for f in CORPUS.glob("*.json") if "wire" in json.loads(f.read_text()))


def _series(case: dict) -> pd.Series:
    # Times arrive as the strings a CSV holds, so the encoder's own parsing is
    # what the corpus pins.
    values = case["values"]
    if case["kind"] in ("f64", "q8"):
        return pd.Series([np.nan if v is None else v for v in values], dtype=float)
    if "zone" in case:  # a typed zoned column (a parquet one): the instants, in that zone
        return pd.Series(pd.to_datetime(values, utc=True)).dt.tz_convert(case["zone"])
    return pd.Series(values, dtype=object)


def test_the_corpus_covers_every_kind():
    kinds = {json.loads(f.read_text())["kind"] for f in FILES}
    assert kinds == {"f64", "time", "cat", "q8", "bits"}


@pytest.mark.parametrize(
    "path", [f for f in FILES if f.name != "bits-nine-rows.json"], ids=lambda p: p.stem
)
def test_the_decoder_reads_the_corpus_as_the_renderer_does(path: Path):
    # The renderer's decodeColumn is held to the same files (wire.test.ts):
    # datums.py places a rule on what the renderer READS, not on raw rows.
    from chart_view.wire import decode_column, epoch_ms

    case = json.loads(path.read_text())
    got = decode_column(case["wire"])
    assert len(got) == len(case["values"])
    for v, g in zip(case["values"], got, strict=True):
        if v is None:
            assert g is None
        elif case["kind"] == "time":
            assert g == epoch_ms(pd.Series([v], dtype=object))[0]
        elif case["kind"] == "q8":
            w = case["wire"]
            assert abs(g - v) <= (w["max"] - w["min"]) / 254 / 2 + 1e-12
        else:
            assert g == v and type(g) is type(v)


@pytest.mark.parametrize(
    "path", [f for f in FILES if f.name != "bits-nine-rows.json"], ids=lambda p: p.stem
)
def test_the_distinct_values_are_the_decoded_ones(path: Path):
    # decode_distinct skips the list per row decode_column builds; it must
    # read the same values, or a category datum is judged on other labels.
    from chart_view.wire import decode_column, decode_distinct

    wire = json.loads(path.read_text())["wire"]
    whole = {v for v in decode_column(wire) if v is not None}
    distinct = decode_distinct(wire)
    assert len(distinct) == len(set(distinct)) and set(distinct) == whole


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_the_encoder_writes_the_corpus(path: Path):
    case = json.loads(path.read_text())
    if case["kind"] == "bits":
        assert bitset(pd.Series(case["values"], dtype=bool)) == case["wire"]
    else:
        assert encode_column(_series(case), case["kind"]) == case["wire"]


INSTANTS = json.loads((CORPUS / "instants.json").read_text())["cases"]


def test_the_instant_corpus_is_how_a_datum_is_read():
    # The oracle file the renderer's parseInstant is held to (a rule's datum
    # never visits the sandbox): a stale file after a change to instant_ms
    # fails here, not in the renderer.
    from chart_view.datums import instant_ms

    assert [instant_ms(c["text"]) for c in INSTANTS] == [c["ms"] for c in INSTANTS]


def test_the_datum_axes_corpus_is_what_validate_and_query_say():
    # The renderer's datum-axes.test.ts reads each case's stored answer and
    # verdict: both must be what the sandbox gives now.
    from chart_view.query import build
    from chart_view.spec import parse_spec
    from chart_view.validate import check

    doc = json.loads((CORPUS / "datum-axes.json").read_text())
    # the writer's own frames: a zoned column's instants in its zone (P26)
    script = importlib.util.spec_from_file_location("write_datum_corpus", DATUM_SCRIPT)
    assert script is not None and script.loader is not None
    writer = importlib.util.module_from_spec(script)
    script.loader.exec_module(writer)
    for c in doc["cases"]:
        frame = writer.frame_of(c["data"], c.get("zones", {}))
        text = json.dumps(c["spec"])
        assert (not check(text, lambda _s, f=frame: f).errors) == c["placed"], c["name"]
        assert build(parse_spec(text), frame) == c["answer"], c["name"]
        if "zones" in c and c["placed"] and c["spec"]["layer"][0]["mark"] != "grid":
            rule = c["spec"]["layer"][-1]["encoding"]
            datum = (rule.get("y") or rule["x"])["datum"]
            assert writer.wall_at(datum, c["zones"]["t"]) == c["at"], c["name"]


def test_a_placed_datum_is_where_the_same_text_in_the_data_is():
    # A rule at "2024-03-01T12:00" sits on the point whose column holds that
    # text: a placed datum is read by the data's own reader.
    from chart_view.wire import epoch_ms

    placed = [c for c in INSTANTS if c["ms"] is not None]
    got = epoch_ms(pd.Series([c["text"] for c in placed], dtype=object)).tolist()
    assert got == [c["ms"] for c in placed]


CANON = json.loads((CORPUS / "canon.json").read_text())["cases"]


@pytest.mark.parametrize("case", CANON, ids=lambda c: repr(c["value"]))
def test_canon_is_the_string_a_browser_would_write(case):
    # `wire.test.ts` holds JavaScript's String(v) to the same table: a selection
    # in the chart (PR 3) writes that string into a marking, so the one written
    # here must compare equal to it.
    assert canon(case["value"]) == case["text"]


@pytest.mark.parametrize(
    ("value", "text"),
    [(np.bool_(True), "true"), (np.int64(7), "7"), (np.float64(2.0), "2")],
)
def test_canon_reads_numpy_scalars_like_python_ones(value, text):
    assert canon(value) == text


@pytest.mark.parametrize("value", [None, float("nan"), pd.NaT, math.inf])
def test_canon_has_no_string_for_a_missing_or_infinite_value(value):
    assert canon(value) is None


def test_a_category_wider_than_a_byte_uses_two():
    s = pd.Series([f"v{i}" for i in range(300)], dtype=object)
    wire = encode_column(s, "cat")
    assert wire["width"] == 2 and len(wire["levels"]) == 300


def test_a_category_wider_than_two_bytes_uses_four():
    s = pd.Series(range(70000), dtype=object)
    assert encode_column(s, "cat")["width"] == 4


def test_a_category_of_mixed_types_still_encodes():
    # A key column holding "7" and 7 is ordinary in hand-made CSVs; pandas
    # orders mixed levels numbers-first rather than refusing.
    wire = encode_column(pd.Series(["b", 1, "a", 1], dtype=object), "cat")
    assert wire["levels"] == [1, "a", "b"]


def test_levels_with_no_order_between_them_keep_first_seen_order():
    s = pd.Series([pd.Timestamp("2024-01-01"), 1.5, pd.Timestamp("2024-01-01")], dtype=object)
    wire = encode_column(s, "cat")
    assert wire["levels"] == ["2024-01-01 00:00:00", 1.5]


def test_a_level_json_cannot_carry_is_sent_as_its_marking_string():
    s = pd.Series([pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-01")], dtype=object)
    assert encode_column(s, "cat")["levels"] == ["2024-01-01 00:00:00", "2024-01-02 00:00:00"]
    assert encode_column(pd.Series([math.inf, 1.0], dtype=object), "cat")["levels"] == [1.0, None]


@pytest.mark.parametrize(
    ("zone", "name"),
    [
        ("Asia/Taipei", "Asia/Taipei"),
        (zoneinfo.ZoneInfo("America/New_York"), "America/New_York"),
        (pytz.timezone("Europe/Berlin"), "Europe/Berlin"),
        ("UTC", "UTC"),
        (dt.timezone(dt.timedelta(hours=8)), "+08:00"),
        (dt.timezone(-dt.timedelta(hours=5, minutes=30)), "-05:30"),
        (pytz.FixedOffset(345), "+05:45"),
    ],
    ids=["str", "zoneinfo", "pytz", "utc", "fixed-east", "fixed-west", "pytz-fixed"],
)
def test_a_zoned_time_column_names_its_zone(zone, name):
    """#847/#848 P14: what the renderer shows a zoned column in -- an IANA name
    the browser's Intl knows, or a fixed offset as +HH:MM (which it formats by
    hand)."""
    utc = pd.Series(pd.to_datetime(["2026-02-28T16:00Z", None, "2026-03-01T04:30Z"], utc=True))
    wire = encode_column(utc.dt.tz_convert(zone), "time")
    assert wire == {**encode_column(utc.dt.tz_localize(None), "time"), "zone": name}


def test_a_zone_less_time_column_names_none():
    assert "zone" not in encode_column(pd.Series(pd.to_datetime(["2026-03-01"])), "time")
    assert "zone" not in encode_column(pd.Series(["2026-03-01T00:00+08:00"], dtype=object), "time")


def test_q8_of_a_constant_column_is_level_zero():
    wire = encode_column(pd.Series([2.0, 2.0]), "q8")
    assert wire["min"] == wire["max"] == 2.0
