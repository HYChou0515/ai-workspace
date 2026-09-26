/**
 * `whereNames`: the columns a `where:` expression reads (#847/#848 PR 5 P41
 * row 21), held to pandas by `wire-corpus/where-names.json` (the sandbox's
 * `scripts/write_where_corpus.py`); the sandbox's test_rules.py holds its own
 * lexer to the same file.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { whereNames } from "./rules";

const file = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "wire-corpus", "where-names.json");
const cases = (JSON.parse(readFileSync(file, "utf8")) as { cases: { where: string; columns: string[] }[] }).cases;

describe("whereNames", () => {
  it.each(cases.map((c) => [c.where, c.columns] as const))("%s reads what pandas reads", (where, columns) => {
    expect([...whereNames(where)].sort()).toEqual(columns);
  });

  // pandas refuses these (or they are not a highlight), so the corpus cannot
  // hold them; the lexer still reads them without inventing a column
  it.each([
    ["@limit < value", ["value"]],
    ["item == 'open", ["item"]],
    ["`open > 1", []],
    ["r'raw' == item", ["item"]],
    ["value == 1.5j", ["value"]],
    ["value > 0x1F", ["value"]],
  ])("%s beyond what pandas evaluates", (where, names) => {
    expect([...whereNames(where)].sort()).toEqual(names);
  });
});
