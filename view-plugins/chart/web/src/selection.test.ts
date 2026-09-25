/**
 * What a brush, a lasso or a legend click selects, in LAYER ROWS — the event
 * PR 3 turns into a marking write — and which rows a highlight lights.
 */
import { describe, expect, it } from "vitest";

import { toOption } from "./option";
import { gridSelectionLit, selectionFromBrush, selectionFromLegend, selectionValues } from "./selection";
import { answer, base, cat, f64, layer, q8 } from "./testAnswer";

const scatter = {
  ...base,
  keys: ["wafer"],
  mark: "scatter",
  encoding: {
    x: { field: "a", type: "quantitative" },
    y: { field: "b", type: "quantitative" },
    color: { field: "lot", type: "nominal" },
  },
};
const scatterAnswer = answer(
  layer("scatter", 4, {
    a: f64([1, 2, 3, 4]),
    b: f64([5, 6, 7, 8]),
    lot: cat(["A", "B", "A", "B"]),
    wafer: cat([7, 8, 9, 10]),
  }),
);
// series 0 = lot A = rows [0, 2]; series 1 = lot B = rows [1, 3]

describe("brush and lasso", () => {
  it("maps the selected data points of each series back to layer rows", () => {
    const built = toOption(scatter, scatterAnswer);
    const sel = selectionFromBrush(
      {
        batch: [
          {
            areas: [{ brushType: "rect" }],
            selected: [
              { seriesIndex: 0, dataIndex: [1] },
              { seriesIndex: 1, dataIndex: [0, 1] },
            ],
          },
        ],
      },
      built,
    );
    expect(sel).toEqual([{ source: "brush", layer: 0, rows: [1, 2, 3] }]);
  });

  it("calls a polygon a lasso", () => {
    const built = toOption(scatter, scatterAnswer);
    const sel = selectionFromBrush(
      { batch: [{ areas: [{ brushType: "polygon" }], selected: [{ seriesIndex: 0, dataIndex: [0] }] }] },
      built,
    );
    expect(sel).toEqual([{ source: "lasso", layer: 0, rows: [0] }]);
  });

  it("clears to nothing when the brush is removed", () => {
    const built = toOption(scatter, scatterAnswer);
    expect(selectionFromBrush({ batch: [{ areas: [], selected: [] }] }, built)).toEqual([]);
    // ECharts fires one on setup with no areas at all.
    expect(selectionFromBrush({ batch: [{}] }, built)).toEqual([]);
    expect(selectionFromBrush({ batch: [] }, built)).toEqual([]);
  });

  it("hit-tests grid cells by their centre, since the raster has no points", () => {
    const grid = {
      ...base,
      mark: "grid",
      encoding: {
        x: { field: "x", type: "ordinal" },
        y: { field: "y", type: "ordinal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    // A 3x2 lattice; rows in data order.
    const a = answer(
      layer("grid", 6, {
        x: f64([0, 1, 2, 0, 1, 2]),
        y: f64([0, 0, 0, 1, 1, 1]),
        v: q8([1, 2, 3, 4, 5, 6], 0, 1),
      }),
    );
    const built = toOption(grid, a);
    // Axis values are cell indices: a rect over x 0.5..2.5, y -0.5..0.5 holds
    // the centres (1,0) and (2,0) — data rows 1 and 2.
    const rect = selectionFromBrush(
      { batch: [{ areas: [{ brushType: "rect", coordRange: [[0.5, 2.5], [-0.5, 0.5]] }], selected: [] }] },
      built,
    );
    expect(rect).toEqual([{ source: "brush", layer: 0, rows: [1, 2] }]);
    // A triangle (hypotenuse y = x - 0.2) holding the centres (0,0), (0,1) and
    // (1,1) clear of its edges; (1,0) and (2,1) lie outside.
    const lasso = selectionFromBrush(
      {
        batch: [{ areas: [{ brushType: "polygon", coordRange: [[-0.4, -0.6], [-0.4, 1.4], [1.6, 1.4]] }], selected: [] }],
      },
      built,
    );
    expect(lasso).toEqual([{ source: "lasso", layer: 0, rows: [0, 3, 4] }]);
  });
});

describe("legend", () => {
  it("selects the rows still shown when a category is hidden", () => {
    const built = toOption(scatter, scatterAnswer);
    expect(selectionFromLegend({ A: true, B: false }, built)).toEqual([{ source: "legend", layer: 0, rows: [0, 2] }]);
  });

  it("selects nothing when every category is shown", () => {
    const built = toOption(scatter, scatterAnswer);
    expect(selectionFromLegend({ A: true, B: true }, built)).toEqual([]);
  });
});

describe("selectionValues", () => {
  it("gives each key's values over the selected rows, as marking strings, once each", () => {
    const values = selectionValues({ source: "brush", layer: 0, rows: [0, 2, 1] }, scatterAnswer, ["wafer", "lot", "gone"]);
    expect(values).toEqual({ wafer: ["7", "9", "8"], lot: ["A", "B"] });
  });
});

describe("gridSelectionLit (#847/#848 P18)", () => {
  const grid = layer("grid", 3, { x: f64([0, 1, 2]), y: f64([0, 0, 0]), v: q8([0, 1, 2], 0, 2) });
  const scatter = layer("scatter", 2, { a: f64([1, 2]), b: f64([1, 2]) }, { highlight: btoa(String.fromCharCode(0b10)), lit: 1 });

  it("lights the grid rows taken, and leaves another layer its own highlight", () => {
    const a = answer(grid, scatter);
    expect(gridSelectionLit(a, [{ source: "lasso", layer: 0, rows: [2] }, { source: "lasso", layer: 0, rows: [0] }])).toEqual([
      [true, false, true],
      [false, true],
    ]);
  });

  it("lights nothing when no grid rows were taken", () => {
    const a = answer(grid, scatter);
    expect(gridSelectionLit(a, [])).toBeUndefined();
    // a scatter's own points are styled by ECharts' brush already
    expect(gridSelectionLit(a, [{ source: "brush", layer: 1, rows: [0] }])).toBeUndefined();
  });
});
