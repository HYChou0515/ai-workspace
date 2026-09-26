"""`rules.where_names`: the columns a `where:` expression reads (#847/#848
PR 5 P41 row 21), held to pandas by `wire-corpus/where-names.json`
(`scripts/write_where_corpus.py`); the renderer's `rules.test.ts` holds its
own lexer to the same file."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from chart_view.rules import where_names

HERE = Path(__file__).resolve()
CORPUS = json.loads((HERE.parents[2] / "wire-corpus" / "where-names.json").read_text())


def _writer():
    script = importlib.util.spec_from_file_location(
        "write_where_corpus", HERE.parents[1] / "scripts" / "write_where_corpus.py"
    )
    assert script is not None and script.loader is not None
    writer = importlib.util.module_from_spec(script)
    script.loader.exec_module(writer)
    return writer


def test_the_where_corpus_is_what_pandas_says():
    assert CORPUS["cases"] == _writer().cases()


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda c: c["where"])
def test_where_names_reads_the_columns_pandas_reads(case):
    assert sorted(where_names(case["where"])) == case["columns"]


@pytest.mark.parametrize(
    ("where", "names"),
    [
        ("@limit < value", ["value"]),  # a local variable is no column
        ("item == 'open", ["item"]),  # an unclosed text runs to the end
        ("`open > 1", []),  # so does an unclosed backtick
        ("r'raw' == item", ["item"]),  # a text's prefix is no column
        ("value == 1.5j", ["value"]),
        ("value > 0x1F", ["value"]),
    ],
)
def test_where_names_beyond_what_pandas_evaluates(where, names):
    # pandas refuses these (or they are not a highlight), so the corpus cannot
    # hold them; the lexer still reads them without inventing a column
    assert sorted(where_names(where)) == names
