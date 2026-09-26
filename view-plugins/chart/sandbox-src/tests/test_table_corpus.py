"""`wire-corpus/table-rows.json` is what a chart writes for a table's rows (#847/#848 PR 5).

The browser's table reads a marking by these strings (`markingRows.test.ts`
holds it to this file), so the file must stay the chart's own answer: a
change to how a chart reads a source reddens here until the corpus is
rewritten, and then the browser's side is checked against the new one.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPT = HERE.parents[1] / "scripts" / "write_table_corpus.py"
CORPUS = HERE.parents[2] / "wire-corpus" / "table-rows.json"


def _script():
    spec = importlib.util.spec_from_file_location("write_table_corpus", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_corpus_is_the_charts_own_answer():
    assert _script().cases() == json.loads(CORPUS.read_text())


def test_every_column_of_every_case_is_there_row_for_row():
    # A case whose chart answer dropped rows (a filter, an aggregate) would
    # pair row i of the table with some other row of the chart.
    doc = json.loads(CORPUS.read_text())
    for case in doc["tables"]:
        rows = [line for line in case["text"].splitlines()[1:] if line]
        assert all(len(v) == len(rows) for v in case["columns"].values()), case["name"]
    for case in doc["entities"]:
        assert all(len(v) == len(case["records"]) for v in case["columns"].values())
