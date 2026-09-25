/**
 * #847/#848 PR 5 P30 against REAL ECharts (SSR, SVG): every mark a person can
 * select from. ECharts 5.6 gives only bar / candlestick / scatter /
 * effectScatter a brush selector (P28 added line, and so area; a grid selects
 * through its cells), so a box or a lasso over a heatmap, a boxplot or an
 * errorbar selected none of their rows. These drive a real instance and read
 * the `brushselected` it fires, as echarts.brush.test.ts does.
 */
import * as echarts from "echarts/core";
import { BrushComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components (and their brush selectors)
import { toOption } from "./option";
import { type BrushSelected, type Selection, selectionFromBrush } from "./selection";
import { answer, base, cat, f64, layer, q8 } from "./testAnswer";

echarts.use([SVGRenderer, BrushComponent]);

type Area =
  | { brushType: "rect"; coordRange: [[number, number], [number, number]] }
  | { brushType: "polygon"; coordRange: [number, number][] };

/** Brush `area` (in DATA coordinates, fractions of a category index allowed)
 * the way a person's drag does: as PIXELS. On a category axis ECharts reads a
 * dispatched `coordRange` as whole indices, which no drag is: a drag keeps its
 * pixels (echarts BrushTargetManager `__rangeOffset`). */
async function brushed(doc: Record<string, unknown>, a: ReturnType<typeof answer>, area: Area): Promise<Selection[]> {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  const events: BrushSelected[] = [];
  chart.on("brushselected", (e) => {
    events.push(e as BrushSelected);
  });
  chart.setOption(built.option, true);
  // Both axes here are linear in pixels (a category's bands are evenly spaced).
  const at = (p: number[]) => chart.convertToPixel({ gridIndex: 0 }, p) as number[];
  const [o, ex, ey] = [at([0, 0]), at([1, 0]), at([0, 1])];
  const px = ([x, y]: number[]): number[] => [o[0]! + x! * (ex[0]! - o[0]!), o[1]! + y! * (ey[1]! - o[1]!)];
  const sorted = (m: number, n: number) => [Math.min(m, n), Math.max(m, n)];
  let range: number[][];
  if (area.brushType === "rect") {
    const [[x0, x1], [y0, y1]] = area.coordRange;
    const [p, q] = [px([x0, y0]), px([x1, y1])];
    range = [sorted(p[0]!, q[0]!), sorted(p[1]!, q[1]!)];
  } else range = area.coordRange.map(px);
  chart.dispatchAction({ type: "brush", areas: [{ brushType: area.brushType, range }] });
  await new Promise((r) => setTimeout(r, 400)); // the option debounces brush events 250 ms
  chart.dispose();
  return selectionFromBrush(events.at(-1) as BrushSelected, built);
}

// A 3 x 2 heatmap: row i is the cell at x index i % 3, y index floor(i / 3).
const HEAT = {
  ...base,
  mark: "heatmap",
  encoding: {
    x: { field: "x", type: "nominal" },
    y: { field: "y", type: "nominal" },
    color: { field: "v", type: "quantitative" },
  },
};
const heat = (v: (number | null)[] = [1, 2, 3, 4, 5, 6]) =>
  answer(layer("heatmap", 6, { x: cat(["a", "b", "c", "a", "b", "c"]), y: cat(["p", "p", "p", "q", "q", "q"]), v: f64(v) }));

describe("brushing a heatmap, against real ECharts", () => {
  it("a box selects the cells whose centre it holds", async () => {
    // x 0.3..2.2 takes the centres of b and c (1, 2), not a's (0) though it
    // overlaps a's cell; y -0.4..0.4 takes row p only
    const sel = await brushed(HEAT, heat(), { brushType: "rect", coordRange: [[0.3, 2.2], [-0.4, 0.4]] });
    expect(sel).toEqual([{ source: "brush", layer: 0, rows: [1, 2] }]);
  });

  it("a lasso selects the cells whose centre it holds", async () => {
    // a triangle around the centres (0, 0), (0, 1) and (1, 1), clear of (1, 0)
    const sel = await brushed(HEAT, heat(), {
      brushType: "polygon",
      coordRange: [[-0.3, -0.4], [-0.3, 1.4], [1.6, 1.4]],
    });
    expect(sel).toEqual([{ source: "lasso", layer: 0, rows: [0, 3, 4] }]);
  });

  it("a cell with no value is not drawn, and so not selected", async () => {
    const sel = await brushed(HEAT, heat([1, null, 3, 4, 5, 6]), { brushType: "rect", coordRange: [[-0.5, 2.5], [-0.5, 0.5]] });
    expect(sel).toEqual([{ source: "brush", layer: 0, rows: [0, 2] }]);
  });
});

// Three groups a, b, c: boxes q1..q3 = 3..7, 13..17, 23..27; whiskers 1 below, 1 above.
const BOX = {
  ...base,
  mark: "boxplot",
  encoding: { x: { field: "g", type: "nominal" }, y: { field: "v", type: "quantitative" } },
};
const boxes = answer(
  layer(
    "boxplot",
    3,
    {
      g: cat(["a", "b", "c"]),
      $lo: f64([2, 12, 22]),
      $q1: f64([3, 13, 23]),
      $mid: f64([5, 15, 25]),
      $q3: f64([7, 17, 27]),
      $hi: f64([8, 18, 28]),
    },
    { outliers: { rows: 1, columns: { g: cat(["a"]), v: f64([15]) } } },
  ),
);

describe("brushing a boxplot, against real ECharts", () => {
  it("a box over part of a group's box selects that group", async () => {
    // y 14..16 crosses b's box only; x spans every group
    const sel = await brushed(BOX, boxes, { brushType: "rect", coordRange: [[-0.5, 2.5], [14, 16]] });
    expect(sel).toEqual([{ source: "brush", layer: 0, rows: [1] }]);
  });

  it("a box over a group's whisker only, or beside its box, selects nothing", async () => {
    // y 7.5..7.9 is a's whisker, above its box; x 1.4..1.6 is between b and c
    expect(await brushed(BOX, boxes, { brushType: "rect", coordRange: [[-0.5, 2.5], [7.5, 7.9]] })).toEqual([]);
    expect(await brushed(BOX, boxes, { brushType: "rect", coordRange: [[1.4, 1.6], [0, 30]] })).toEqual([]);
  });

  it("a lasso over two boxes selects both groups", async () => {
    const sel = await brushed(BOX, boxes, {
      brushType: "polygon",
      coordRange: [[-0.2, 4], [1.2, 14], [1.2, 16], [-0.2, 6]],
    });
    expect(sel).toEqual([{ source: "lasso", layer: 0, rows: [0, 1] }]);
  });
});

// Three errorbars at a, b, c: 1..3, 11..13, 21..23.
const BAR = {
  ...base,
  mark: "errorbar",
  encoding: { x: { field: "g", type: "nominal" }, y: { field: "v", type: "quantitative" } },
};
const bars = answer(
  layer("errorbar", 3, { g: cat(["a", "b", "c"]), $lo: f64([1, 11, 21]), $mid: f64([2, 12, 22]), $hi: f64([3, 13, 23]) }),
);

describe("brushing an errorbar, against real ECharts", () => {
  it("a box across a bar's centre line selects that group", async () => {
    // y 12..30 crosses b's and c's lines; x 0.9..2.1 holds b and c, not a
    const sel = await brushed(BAR, bars, { brushType: "rect", coordRange: [[0.9, 2.1], [12, 30]] });
    expect(sel).toEqual([{ source: "brush", layer: 0, rows: [1, 2] }]);
  });

  it("a box beside every line, or past its ends, selects nothing", async () => {
    expect(await brushed(BAR, bars, { brushType: "rect", coordRange: [[0.2, 0.8], [0, 30]] })).toEqual([]);
    expect(await brushed(BAR, bars, { brushType: "rect", coordRange: [[-0.5, 2.5], [4, 10]] })).toEqual([]);
  });

  it("a lasso across a line selects that group", async () => {
    const sel = await brushed(BAR, bars, {
      brushType: "polygon",
      coordRange: [[-0.3, 2], [0.3, 2], [0.3, 2.5], [-0.3, 2.5]],
    });
    expect(sel).toEqual([{ source: "lasso", layer: 0, rows: [0] }]);
  });
});

describe("a grid beside the errorbar's selector, against real ECharts", () => {
  // A grid is drawn by a custom series too (its image). The errorbar's
  // selector must leave it be: its cells are still found by their centre.
  it("a box over a grid still selects the cells whose centre it holds", async () => {
    const grid = {
      ...base,
      mark: "grid",
      encoding: {
        x: { field: "x", type: "ordinal" },
        y: { field: "y", type: "ordinal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const a = answer(layer("grid", 6, { x: f64([0, 1, 2, 0, 1, 2]), y: f64([0, 0, 0, 1, 1, 1]), v: q8([1, 2, 3, 4, 5, 6], 0, 1) }));
    const built = toOption(grid, a);
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
    const events: BrushSelected[] = [];
    chart.on("brushselected", (e) => {
      events.push(e as BrushSelected);
    });
    chart.setOption(built.option, true);
    // bound to the grid's axes, as the toolbox's brush is: the grid path reads coordRange
    chart.dispatchAction({ type: "brush", areas: [{ brushType: "rect", xAxisIndex: 0, coordRange: [[0.5, 2.5], [-0.5, 0.5]] }] });
    await new Promise((r) => setTimeout(r, 400));
    chart.dispose();
    expect(selectionFromBrush(events.at(-1) as BrushSelected, built)).toEqual([{ source: "brush", layer: 0, rows: [1, 2] }]);
  });
});
