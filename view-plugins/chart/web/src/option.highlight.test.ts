/**
 * `highlight:` in the drawn option: the unlit rows are dimmed in the data
 * itself (P7). Not through ECharts' hover states — every highlight / downplay
 * action, a mouse passing over the chart included, starts with `allLeaveBlur`,
 * so a highlight kept in those states was gone the first time anyone moved the
 * mouse (and a second series' highlight blurred the first's lit points; both
 * measured on real ECharts). A style on the data item has no such lifetime.
 */
import { describe, expect, it } from "vitest";

import { toOption } from "./option";
import { answer, base, cat, f64, layer, q8 } from "./testAnswer";

// Rows 1 and 2 lit (bits 0b0110).
const LIT = btoa(String.fromCharCode(0b0110));
const dim = { itemStyle: { opacity: 0.15 } };
const lit = { highlight: LIT, lit: 2 };

describe("highlight", () => {
  it("dims the rows the highlight leaves unlit, in the drawn data itself", () => {
    const spec = {
      ...base,
      mark: "scatter",
      encoding: {
        x: { field: "a", type: "quantitative" },
        y: { field: "b", type: "quantitative" },
        color: { field: "lot", type: "nominal" },
      },
    };
    const a = answer(
      layer("scatter", 4, { a: f64([1, 2, 3, 4]), b: f64([5, 6, 7, 8]), lot: cat(["A", "B", "A", "B"]) }, lit),
    );
    const s = toOption(spec, a).option.series as { data: unknown[] }[];
    // Series A = rows 0 (unlit) and 2 (lit); series B = rows 1 (lit) and 3 (unlit).
    expect(s[0].data).toEqual([{ value: [1, 5], ...dim }, [3, 7]]);
    expect(s[1].data).toEqual([[2, 6], { value: [4, 8], ...dim }]);
  });

  it("dims bars, pie slices and heatmap cells the same way", () => {
    const four = { k: cat(["a", "b", "c", "d"]), v: f64([1, 2, 3, 4]) };
    const bar = {
      ...base,
      mark: "bar",
      encoding: { x: { field: "k", type: "nominal" }, y: { field: "v", type: "quantitative" } },
    };
    expect((toOption(bar, answer(layer("bar", 4, four, lit))).option.series as { data: unknown[] }[])[0].data).toEqual([
      { value: [0, 1], ...dim },
      [1, 2],
      [2, 3],
      { value: [3, 4], ...dim },
    ]);
    const pie = {
      ...base,
      mark: "pie",
      encoding: { theta: { field: "v", type: "quantitative" }, color: { field: "k", type: "nominal" } },
    };
    expect((toOption(pie, answer(layer("pie", 4, four, lit))).option.series as { data: unknown[] }[])[0].data).toEqual([
      { name: "a", value: 1, ...dim },
      { name: "b", value: 2 },
      { name: "c", value: 3 },
      { name: "d", value: 4, ...dim },
    ]);
    const heat = {
      ...base,
      mark: "heatmap",
      encoding: {
        x: { field: "k", type: "nominal" },
        y: { field: "k", type: "nominal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const cells = (toOption(heat, answer(layer("heatmap", 4, four, lit))).option.series as { data: unknown[] }[])[0].data;
    expect(cells[0]).toEqual({ value: [0, 0, 1], ...dim });
    expect(cells[1]).toEqual([1, 1, 2]);
  });

  it("keeps per-point styles by leaving large mode off when there is a highlight", () => {
    const n = 2001;
    const spec = {
      ...base,
      mark: "scatter",
      encoding: { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } },
    };
    const col = f64(Array.from({ length: n }, (_, i) => i));
    const bits = btoa(String.fromCharCode(...new Uint8Array(Math.ceil(n / 8)).fill(1)));
    const s = toOption(spec, answer(layer("scatter", n, { a: col, b: col }, { highlight: bits, lit: 251 }))).option
      .series as { large?: boolean }[];
    expect(s[0].large).toBeUndefined();
  });

  it("fades a grid's unlit cells in the raster itself", () => {
    const grid = {
      ...base,
      mark: "grid",
      encoding: {
        x: { field: "x", type: "ordinal" },
        y: { field: "y", type: "ordinal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const a = answer(layer("grid", 4, { x: f64([0, 1, 0, 1]), y: f64([0, 0, 1, 1]), v: q8([0, 0, 0, 0], 0, 1) }, lit));
    const [g] = toOption(grid, a).grids;
    const alpha = (row: number) => {
      for (let r = 0; r < g.cells.height; r++)
        for (let c = 0; c < g.cells.width; c++)
          if (g.cells.rowAt(c, r) === row) return g.image.data[(r * g.cells.width + c) * 4 + 3];
      return -1;
    };
    expect([alpha(0), alpha(1), alpha(2), alpha(3)]).toEqual([64, 255, 255, 64]);
  });
});
