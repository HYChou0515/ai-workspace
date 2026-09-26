/**
 * A table reads a marking by the strings a chart writes (#847/#848 PR 5 P1).
 *
 * The oracle is `view-plugins/chart/wire-corpus/table-rows.json`: per column,
 * the marking string a chart keyed on that column writes for each row — the
 * chart's own `build` + `canon` (`test_table_corpus.py` holds the file to it;
 * `tests/view_plugins/test_table_corpus.py` holds its entity records to what
 * the API sends). Here the browser's reading of the same cells must give the
 * same strings, and a row must be lit exactly when `isLit` lights the chart's
 * row under the same marking — the platform's one rule.
 */
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { parseCsv } from "../src/renderers/csv";
import { isLit, type Marking } from "../src/lib/markings";
import { csvMarkingRows, entityMarkingRow, litRows } from "../src/lib/markingRows";

const CORPUS = join(
  resolve(fileURLToPath(new URL("..", import.meta.url))),
  "..",
  "view-plugins",
  "chart",
  "wire-corpus",
  "table-rows.json",
);

type Columns = Record<string, (string | null)[]>;
type TableCase = { name: string; delimiter: string; text: string; columns: Columns };
type EntityCase = { name: string; records: { number: number; fields: Record<string, unknown> }[]; columns: Columns };
const doc = JSON.parse(readFileSync(CORPUS, "utf8")) as { tables: TableCase[]; entities: EntityCase[] };

/** Row i as the chart holds it: each column's string, a missing one left out. */
function chartRows(columns: Columns, n: number): Record<string, string>[] {
  return Array.from({ length: n }, (_, i) => {
    const row: Record<string, string> = {};
    for (const [c, values] of Object.entries(columns)) {
      const v = values[i];
      if (v !== null && v !== undefined) row[c] = v;
    }
    return row;
  });
}

/** Markings to light by: every single value of every column, and each pair of
 * columns at their first row's values. */
function markingsFor(columns: Columns): Marking[] {
  const out: Marking[] = [];
  const cols = Object.keys(columns);
  for (const c of cols) {
    for (const v of new Set(columns[c]!)) if (v !== null) out.push({ [c]: new Set([v]) });
  }
  for (const a of cols) {
    for (const b of cols) {
      const va = columns[a]![0];
      const vb = columns[b]![0];
      if (a < b && va != null && vb != null) out.push({ [a]: new Set([va]), [b]: new Set([vb]) });
    }
  }
  return out;
}

describe.each(doc.tables)("a $name table", (c) => {
  const rows = csvMarkingRows(parseCsv(c.text, c.delimiter));
  const n = rows.length;

  it("reads each cell as the string the chart writes for it", () => {
    expect(rows).toEqual(chartRows(c.columns, n));
  });

  it("lights exactly the rows isLit lights on the chart's rows", () => {
    const chart = chartRows(c.columns, n);
    for (const m of markingsFor(c.columns)) {
      expect(litRows(rows, m)).toEqual(chart.map((r) => isLit(r, m)));
    }
  });
});

describe.each(doc.entities)("an $name table", (c) => {
  const rows = c.records.map((r) => entityMarkingRow(r));

  it("reads each field as the string the chart writes for it", () => {
    expect(rows).toEqual(chartRows(c.columns, c.records.length));
  });

  it("lights exactly the rows isLit lights on the chart's rows", () => {
    const chart = chartRows(c.columns, c.records.length);
    for (const m of markingsFor(c.columns)) {
      expect(litRows(rows, m)).toEqual(chart.map((r) => isLit(r, m)));
    }
  });
});
