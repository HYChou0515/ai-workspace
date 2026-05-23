import { describe, expect, it } from "vitest";

import { cellSource, parseNotebook, pickMime } from "./types";

describe("parseNotebook", () => {
  it("parses a minimal nbformat v4 notebook", () => {
    const nb = parseNotebook(
      JSON.stringify({
        cells: [
          { cell_type: "code", source: ["print('hi')"], outputs: [], execution_count: null },
          { cell_type: "markdown", source: "# heading" },
        ],
        metadata: {},
        nbformat: 4,
        nbformat_minor: 5,
      }),
    );
    expect(nb.cells).toHaveLength(2);
    expect(nb.cells[0]?.cell_type).toBe("code");
    expect(nb.nbformat).toBe(4);
  });

  it("defaults to empty cells when cells is missing", () => {
    const nb = parseNotebook(JSON.stringify({}));
    expect(nb.cells).toEqual([]);
  });

  it("throws on non-JSON input", () => {
    expect(() => parseNotebook("not json")).toThrow();
  });
});

describe("cellSource", () => {
  it("joins array-source into a single string", () => {
    expect(
      cellSource({ cell_type: "code", source: ["a\n", "b\n"] }),
    ).toBe("a\nb\n");
  });

  it("returns string-source untouched", () => {
    expect(cellSource({ cell_type: "markdown", source: "# x" })).toBe("# x");
  });
});

describe("pickMime", () => {
  it("prefers image/png over text/html over text/plain", () => {
    expect(pickMime({
      "image/png": "BASE64",
      "text/html": "<p>x</p>",
      "text/plain": "x",
    })).toEqual({ mime: "image/png", body: "BASE64" });

    expect(pickMime({
      "text/html": "<p>x</p>",
      "text/plain": "x",
    })).toEqual({ mime: "text/html", body: "<p>x</p>" });

    expect(pickMime({ "text/plain": "x" })).toEqual({ mime: "text/plain", body: "x" });
  });

  it("joins array bodies", () => {
    expect(pickMime({ "text/plain": ["a", "b"] })).toEqual({ mime: "text/plain", body: "ab" });
  });

  it("falls back to the first key if no priority match", () => {
    const got = pickMime({ "application/json": '{"a":1}' });
    expect(got?.mime).toBe("application/json");
  });
});
