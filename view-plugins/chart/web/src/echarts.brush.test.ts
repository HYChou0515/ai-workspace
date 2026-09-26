/**
 * #847/#848 PR 5 P28 against REAL ECharts (SSR, SVG): a brush or a lasso over
 * a line or an area selects the rows whose points it covers. ECharts' line
 * series has no brush selector of its own, so before this a line's rows were
 * never selected (PR 2's Done-means lists box brush for every mark). The event
 * shape is ECharts' own, so these drive a real instance and read the
 * `brushselected` it fires.
 */
import * as echarts from "echarts/core";
import { BrushComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components (and a line's brush selector)
import { toOption } from "./option";
import { type BrushSelected, type Selection, selectionFromBrush } from "./selection";
import { answer, base, cat, f64, layer, time } from "./testAnswer";

echarts.use([SVGRenderer, BrushComponent]);

const XS = [1, 2, 3, 4, 5];
const YS = [3, 1, 4, 1, 5];
const XY = { x: { field: "x", type: "quantitative" }, y: { field: "y", type: "quantitative" } };

async function brushed(doc: Record<string, unknown>, a: ReturnType<typeof answer>, area: Record<string, unknown>): Promise<Selection[]> {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  const events: BrushSelected[] = [];
  chart.on("brushselected", (e) => {
    events.push(e as BrushSelected);
  });
  chart.setOption(built.option, true);
  chart.dispatchAction({ type: "brush", areas: [{ xAxisIndex: 0, ...area }] });
  await new Promise((r) => setTimeout(r, 400)); // the option debounces brush events 250 ms
  chart.dispose();
  return selectionFromBrush(events.at(-1) as BrushSelected, built);
}

describe("brushing a line or an area, against real ECharts", () => {
  it("a box over a line selects the rows of the points inside it", async () => {
    const sel = await brushed(
      { ...base, mark: "line", encoding: XY },
      answer(layer("line", XS.length, { x: f64(XS), y: f64(YS) })),
      { brushType: "rect", coordRange: [[1.5, 3.5], [0, 5]] },
    );
    expect(sel).toEqual([{ source: "brush", layer: 0, rows: [1, 2] }]);
  });

  it("the box is tested against where a point is DRAWN: y too", async () => {
    // x 1..5 all inside; only y = 3, 4, 5 fall in [2.5, 6]
    const sel = await brushed(
      { ...base, mark: "line", encoding: XY },
      answer(layer("line", XS.length, { x: f64(XS), y: f64(YS) })),
      { brushType: "rect", coordRange: [[0, 6], [2.5, 6]] },
    );
    expect(sel).toEqual([{ source: "brush", layer: 0, rows: [0, 2, 4] }]);
  });

  it("a lasso over an area selects the rows of the points inside it", async () => {
    const sel = await brushed(
      { ...base, mark: "area", encoding: XY },
      answer(layer("area", XS.length, { x: f64(XS), y: f64(YS) })),
      // a quadrilateral around (3, 4) and (5, 5), clear of every other point
      { brushType: "polygon", coordRange: [[2.5, 3.5], [5.5, 3.5], [5.5, 6], [2.5, 4.5]] },
    );
    expect(sel).toEqual([{ source: "lasso", layer: 0, rows: [2, 4] }]);
  });

  it("a stacked area's points are where the stack draws them, not their own values", async () => {
    const ones = f64([1, 1, 1, 1, 1]);
    const stacked = { type: "area", stack: true };
    // on a time x axis, where ECharts stacks (on two value axes it does not)
    const enc = { x: { field: "x", type: "temporal" }, y: XY.y };
    const days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-06"];
    const xs = time(days);
    const sel = await brushed(
      { ...base, layer: [{ mark: stacked, encoding: enc }, { mark: stacked, encoding: enc }] },
      answer(layer("area", 5, { x: xs, y: ones }), layer("area", 5, { x: xs, y: ones })),
      // the upper layer is drawn at 2; its own values are 1, like the lower one's
      { brushType: "rect", coordRange: [[Date.parse("2024-01-01"), Date.parse("2024-01-07")], [1.5, 2.5]] },
    );
    expect(sel).toEqual([{ source: "brush", layer: 1, rows: [0, 1, 2, 3, 4] }]);
  });

  it("a line split by a colour maps each series' points back to its layer rows", async () => {
    const doc = { ...base, mark: "line", encoding: { ...XY, color: { field: "g", type: "nominal" } } };
    const sel = await brushed(
      doc,
      answer(layer("line", 5, { x: f64(XS), y: f64(YS), g: cat(["a", "b", "a", "b", "a"]) })),
      { brushType: "rect", coordRange: [[1.5, 4.5], [0, 6]] },
    );
    expect(sel).toEqual([{ source: "brush", layer: 0, rows: [1, 2, 3] }]);
  });
});
