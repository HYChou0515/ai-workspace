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
import { answer, base, cat, f64, layer } from "./testAnswer";

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
