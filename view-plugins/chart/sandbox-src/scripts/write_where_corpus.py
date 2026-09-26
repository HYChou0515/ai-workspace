"""Write `wire-corpus/where-names.json`: `where:` expressions and the columns
pandas reads to evaluate each one (#847/#848 PR 5 P41 row 21).

A stack links by its slot and colour only, so `validate` refuses a stacked
layer whose `highlight: {where: ...}` reads any other field -- in the sandbox
AND in the renderer, which cannot run pandas. Both read an expression's
names with the same small lexer (`rules.where_names`, `rules.ts:whereNames`);
this file is their oracle, and pandas decides it: every case's frame holds
a column for each candidate name, and a column is READ when the expression,
evaluated as `df.eval` evaluates a highlight, fails with "name ... is not
defined" once that column is dropped. `test_rules.py` and `rules.test.ts`
hold each lexer to it; `test_rules.py` also checks the file is current.
Rerun after changing a case:

    uv run python scripts/write_where_corpus.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import UndefinedVariableError

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus" / "where-names.json"

NUM = [1.0, 2.0, 3.0]
TEXT = ["p", "q", "r"]
FLAG = [True, False, True]

# (expression, {column: values}): the frame holds every name the expression
# might read, typed so the expression evaluates
CASES: list[tuple[str, dict[str, list[Any]]]] = [
    ("value > 10", {"value": NUM}),
    ("item == 'p' and group in ['a', 'b']", {"item": TEXT, "group": TEXT}),
    ('item == "and group" or value < 2', {"item": TEXT, "group": TEXT, "value": NUM}),
    ("item == 'it\\'s value'", {"item": TEXT, "value": NUM}),
    ("`my value` > 2", {"my value": NUM}),
    ("`a-b` > 1 and region != 'n'", {"a-b": NUM, "region": TEXT}),
    ("abs(value) < 3", {"value": NUM}),
    ("sin(value) > 0 or sqrt(size) > 1", {"value": NUM, "size": NUM}),
    ("value.isin([1, 2])", {"value": NUM, "isin": NUM}),
    ("item.str.startswith('p')", {"item": TEXT, "str": TEXT, "startswith": TEXT}),
    ("a + b * c > d", {"a": NUM, "b": NUM, "c": NUM, "d": NUM}),
    ("not flag", {"flag": FLAG}),
    ("~flag | other", {"flag": FLAG, "other": FLAG}),
    ("flag == True", {"flag": FLAG, "True": FLAG}),
    ("value > 1e5 or value < 2.5e-3", {"value": NUM, "e5": NUM, "e": NUM}),
    ("value > .5 and value < 1_000", {"value": NUM}),
    ("item != item", {"item": TEXT}),
    ("a < b < c", {"a": NUM, "b": NUM, "c": NUM}),
    ("value in (1, 2)", {"value": NUM}),
    ("(item == 'p') & (group != 'a')", {"item": TEXT, "group": TEXT}),
    ("item not in ['p']", {"item": TEXT, "not": TEXT, "in": TEXT}),
    ("Ünïcode_1 > 1", {"Ünïcode_1": NUM}),
    ("value >= 1 and index < 2", {"value": NUM, "index": NUM}),
    ("value ** 2 > 3 and value % 2 == 1", {"value": NUM}),
    ("item == 'p' or item == \"q\"", {"item": TEXT}),
    ("_x > 1", {"_x": NUM}),
]


def _eval(df: pd.DataFrame, expr: str) -> None:
    # as `transforms._query` evaluates a highlight's `where:`
    df.eval(expr)


def read_columns(expr: str, data: dict[str, list[Any]]) -> list[str]:
    frame = pd.DataFrame(data)
    _eval(frame, expr)  # the whole frame evaluates
    read = []
    for column in frame.columns:
        try:
            _eval(frame.drop(columns=[column]), expr)
        except UndefinedVariableError:
            read.append(column)
    return sorted(read)


def cases() -> list[dict[str, Any]]:
    return [{"where": e, "columns": read_columns(e, data)} for e, data in CASES]


def main() -> None:
    doc = {"kind": "where-names", "cases": cases()}
    CORPUS.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(len(doc["cases"]))


if __name__ == "__main__":
    main()
