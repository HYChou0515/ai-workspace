import { describe, expect, it } from "vitest";

import { insertColumn, insertRow, removeColumn, removeRow, sortRows } from "./sheetOps";

const GRID = [
  ["wafer", "qty"],
  ["W01", "120"],
  ["W02", "98"],
];

describe("insertRow", () => {
  it("inserts a blank row of the same width at the given index", () => {
    expect(insertRow(GRID, 1)).toEqual([
      ["wafer", "qty"],
      ["", ""],
      ["W01", "120"],
      ["W02", "98"],
    ]);
  });
});

describe("removeRow", () => {
  it("drops the row at the given index", () => {
    expect(removeRow(GRID, 1)).toEqual([
      ["wafer", "qty"],
      ["W02", "98"],
    ]);
  });

  it("never empties the grid — removing the last row leaves one blank row", () => {
    expect(removeRow([["only"]], 0)).toEqual([[""]]);
  });
});

describe("insertColumn", () => {
  it("inserts a blank cell at the given column in every row", () => {
    expect(insertColumn(GRID, 1)).toEqual([
      ["wafer", "", "qty"],
      ["W01", "", "120"],
      ["W02", "", "98"],
    ]);
  });

  it("does not pad a short row's other cells — a ragged row stays as ragged as it was", () => {
    // Silently widening rows the user did not touch would rewrite the file
    // beyond the requested edit.
    expect(insertColumn([["a", "b", "c"], ["x"]], 2)).toEqual([["a", "b", "", "c"], ["x", ""]]);
  });
});

describe("removeColumn", () => {
  it("drops the given column from every row", () => {
    expect(removeColumn(GRID, 0)).toEqual([["qty"], ["120"], ["98"]]);
  });

  it("never leaves zero columns — removing the last one leaves a blank column", () => {
    expect(removeColumn([["only"], ["x"]], 0)).toEqual([[""], [""]]);
  });
});

describe("sortRows", () => {
  const DATA = [
    ["wafer", "qty"],
    ["W02", "98"],
    ["W01", "120"],
    ["W03", "9"],
  ];

  it("keeps the header row pinned and sorts the rest ascending", () => {
    expect(sortRows(DATA, 0, "asc")).toEqual([
      ["wafer", "qty"],
      ["W01", "120"],
      ["W02", "98"],
      ["W03", "9"],
    ]);
  });

  it("compares numbers as numbers, not as text", () => {
    // Lexicographic order would put "120" before "9".
    expect(sortRows(DATA, 1, "asc").map((r) => r[1])).toEqual(["qty", "9", "98", "120"]);
  });

  it("sorts descending", () => {
    expect(sortRows(DATA, 1, "desc").map((r) => r[1])).toEqual(["qty", "120", "98", "9"]);
  });

  it("puts blanks last regardless of direction, so empty cells never bury the data", () => {
    const withBlank = [["h"], ["b"], [""], ["a"]];
    expect(sortRows(withBlank, 0, "asc").map((r) => r[0])).toEqual(["h", "a", "b", ""]);
    expect(sortRows(withBlank, 0, "desc").map((r) => r[0])).toEqual(["h", "b", "a", ""]);
  });
});
