"""Write `wire-corpus/table-rows.json`: a table's cells and the marking strings
a chart over the same source writes for them (#847/#848 PR 5).

A table follows a marking by comparing its rows with the marking's values as
marking text — the string a chart writes (`wire.canon` over what `query`
sends for a `keys:` column). The table never visits the sandbox, so the
browser has to arrive at the same strings from what it holds:

- a CSV/TSV table holds the file's text, which pandas types column by column
  (`read_csv`: numbers, booleans, missing values), and
- an entity table holds the records as the API sends them (JSON: a date or a
  date-time is text, a whole float is an integer).

Each case below is the chart's answer, read through `build` and `canon` — the
oracle. `markingRows.test.ts` holds the browser's reading to it,
`test_table_corpus.py` holds this file to the chart, and
`tests/view_plugins/test_table_corpus.py` holds each case's `records` to what
the entity API sends. Rerun after changing how a chart reads a source:

    uv run python scripts/write_table_corpus.py

Left out on purpose — the browser cannot tell them apart, so a table and a
chart disagree on them (listed in the table's docs):

- a float written with 17 significant digits in a CSV ("0.30000000000000004"):
  pandas' parser rounds it to a neighbouring double, the browser does not;
- a whole float inside a list (`[1.0]`: JSON sends 1) and a mapping with a
  non-text key (`{1: x}`: JSON keys are text);
- text shaped exactly like an ISO date-time (`"2024-01-02T10:00:00"`, quoted in
  YAML): JSON sends a date-time the same way, and it is read as one.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any

from chart_view.query import build
from chart_view.sources import read_source
from chart_view.wire import canon, decode_column

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus" / "table-rows.json"
ROW = "$row"  # a column no source has: the chart's x and y, one point per row

CSV: dict[str, str] = {
    "numbers": (
        "int,float,int_gap,exp,signed,lead_zero,inf,big,uint\n"
        "1,3.0,1,1e3,+5,007,inf,9007199254740993,9223372036854775808\n"
        "2,0.1,,2E-7,-0,010,-Infinity,2,1\n"
        "3,-2.5,3,1.5e+300,-3,0,1,3,2\n"
        " 4 , .5 ,NA,1e05,-4.,00,2,4,3\n"
    ),
    "booleans": (
        "bool,bool_gap,mixed_case,bool_and_number\n"
        "True,True,tRUE,True\n"
        "False,,FALSE,1\n"
        "TRUE,false,true,False\n"
    ),
    "text": (
        "text,missing,spaced,mixed,date,datetime,overflow,wide_int\n"
        "A1,NA,  x ,1,2024-01-02,2024-01-02 10:00:00,1e400,18446744073709551616\n"
        "B 2,x,x ,True,2024-01-03,2024-01-03T10:00:00Z,1,1\n"
        '"c,3",null, inf,a,2024-01-04,2024-01-04,2,2\n'
        '"say ""hi""",#N/A,3 ,,1999-12-31,,3,3\n'
    ),
    "empty_column": "id,blank\n1,\n2,\n",
}

TSV: dict[str, str] = {
    "tabs": "lot\tvalue\tflag\nA\t1.50\tTrue\nB\t2\tFalse\n",
}

ENTITY_SCHEMA = (
    "path: recs\n"
    "fields:\n"
    "  title: { role: text }\n"
    "  count: { role: text }\n"
    "  ratio: { role: text }\n"
    "  done: { role: text }\n"
    "  due: { role: date }\n"
    "  at: { role: text }\n"
    "  tags: { role: text }\n"
    "  mixed_list: { role: text }\n"
    "  nested: { role: text }\n"
    "  meta: { role: text }\n"
    "  anything: { role: text }\n"
)

ENTITY_RECORDS: list[str] = [
    "title: a\ncount: 3\nratio: 1.0\ndone: true\ndue: 2024-01-02\nat: 2024-01-02T10:00:00\n"
    "tags: [a, b]\nmixed_list: [1, 2.5, true, null]\nnested: [2024-01-02, x]\n"
    "meta: {b: 1, a: x}\nanything: 3\n",
    'title: "it\'s"\ncount: 1152921504606846976\nratio: 0.1\ndone: false\ndue: 2024-03-01\n'
    "at: 2024-01-02 10:00:00.5\ntags: [\"it's\", 'x\"y']\nmixed_list: [1e+21, 0.00001, 1.5e-7]\n"
    "nested: [{k: 1, a: [x]}]\nmeta: {}\nanything: x\n",
    "title: 'say \"hi\"'\ncount: -1\nratio: 2.5\ndone: true\ndue: 2024-01-02\n"
    "at: 2024-01-02T10:00:00Z\ntags: []\n"
    "mixed_list: [100000000000000000.5, -0.0001, 10000000000000000.0, 1234567890123456.8, -0.0]\n"
    "nested: [2024-01-02T10:00:00Z, 2024-01-02T10:00:00.25+08:00]\nmeta: {k: [1, 2]}\n"
    "anything: true\n",
    "title: '3'\ncount: 0\nratio: 1e+21\ndone: false\ndue: 1999-12-31\n"
    'at: 2024-01-02T10:00:00+08:00\ntags: [a\\b, "tab\\there", "\\u00e9\\u00a0"]\n'
    "mixed_list: [[1, [2]], {x: null}]\nnested: []\nmeta: {z: {y: 1}}\nanything: 2024-01-02\n",
    "title: no fields but a title\n",
]


def _json(value: Any) -> Any:
    """`value` as the entity API sends it (pydantic's JSON mode) — held to the
    real serializer by `tests/view_plugins/test_table_corpus.py`."""
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json(v) for v in value]
    if isinstance(value, dt.datetime):
        text = value.isoformat()
        return text[:-6] + "Z" if value.utcoffset() == dt.timedelta(0) else text
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _chart_texts(frame: Any, source: Any) -> dict[str, list[str | None]]:
    """Each column's marking strings, as a chart keyed on it writes them."""
    out: dict[str, list[str | None]] = {}
    for column in frame.columns:
        rows = frame.assign(**{ROW: range(len(frame))})
        spec = {
            "view": "chart",
            "source": source,
            "mark": "scatter",
            "encoding": {
                "x": {"field": ROW, "type": "quantitative"},
                "y": {"field": ROW, "type": "quantitative"},
            },
            "keys": [column],
        }
        [layer] = build(spec, rows)["layers"]
        wire = layer["columns"].get(f"$key.{column}") or layer["columns"][column]
        out[column] = [canon(v) for v in decode_column(wire)]
    return out


def _table_case(name: str, text: str, suffix: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / f"t{suffix}").write_text(text)
        frame = read_source(root, f"t{suffix}")
    return {
        "name": name,
        "delimiter": "\t" if suffix == ".tsv" else ",",
        "text": text,
        "columns": _chart_texts(frame, f"t{suffix}"),
    }


def _entity_case() -> dict[str, Any]:
    from workspace_app.entity.local import read_entity_records

    files = {"/.entity/rec/schema.yaml": ENTITY_SCHEMA}
    files.update({f"/recs/{n}.md": f"---\n{body}---\n" for n, body in enumerate(ENTITY_RECORDS, 1)})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for path, text in files.items():
            target = root / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        records = read_entity_records(root, "rec")
        frame = read_source(root, {"entity": "rec"})
    return {
        "name": "entity",
        "files": files,
        "type": "rec",
        "records": [
            {"number": r["number"], "fields": {k: _json(v) for k, v in r.items() if k != "number"}}
            for r in records
        ],
        "columns": _chart_texts(frame, {"entity": "rec"}),
    }


def cases() -> dict[str, Any]:
    tables = [_table_case(n, t, ".csv") for n, t in CSV.items()]
    tables += [_table_case(n, t, ".tsv") for n, t in TSV.items()]
    return {
        "kind": "table-rows",
        "about": (
            "A table's cells, and per column the marking string a chart keyed on that column "
            "writes for each row (null: none). Written by scripts/write_table_corpus.py."
        ),
        "tables": tables,
        "entities": [_entity_case()],
    }


def main() -> None:
    CORPUS.write_text(json.dumps(cases(), indent=1, ensure_ascii=False) + "\n")
    print(CORPUS)


if __name__ == "__main__":
    main()
