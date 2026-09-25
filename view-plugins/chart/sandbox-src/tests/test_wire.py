"""The wire format `query` answers in, pinned by `wire-corpus/`.

Each corpus file is `{"kind", "values", "wire"}`: this test holds the encoder to
`values → wire`, and the renderer's `wire.test.ts` holds its decoder to
`wire → values`, so the two halves meet on the file rather than on each other.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chart_view.wire import bitset, canon, encode_column

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus"
FILES = sorted(
    f
    for f in CORPUS.glob("*.json")
    if f.name not in ("canon.json", "instants.json", "datum-axes.json")
)


def _series(case: dict) -> pd.Series:
    # Times arrive as the strings a CSV holds, so the encoder's own parsing is
    # what the corpus pins.
    values = case["values"]
    if case["kind"] in ("f64", "q8"):
        return pd.Series([np.nan if v is None else v for v in values], dtype=float)
    return pd.Series(values, dtype=object)


def test_the_corpus_covers_every_kind():
    kinds = {json.loads(f.read_text())["kind"] for f in FILES}
    assert kinds == {"f64", "time", "cat", "q8", "bits"}


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
    from chart_view.validate import instant_ms

    assert [instant_ms(c["text"]) for c in INSTANTS] == [c["ms"] for c in INSTANTS]


def test_the_datum_axes_corpus_is_what_validate_says():
    # The renderer's datum-axes.test.ts reads this file as validate's verdict.
    from chart_view.validate import check

    doc = json.loads((CORPUS / "datum-axes.json").read_text())
    frame = pd.DataFrame(doc["data"])
    got = [
        not check(
            json.dumps({"view": "chart", "source": "a.csv", "layer": c["layer"]}),
            lambda _s: frame,
        ).errors
        for c in doc["cases"]
    ]
    assert got == [c["placed"] for c in doc["cases"]]


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


def test_q8_of_a_constant_column_is_level_zero():
    wire = encode_column(pd.Series([2.0, 2.0]), "q8")
    assert wire["min"] == wire["max"] == 2.0
