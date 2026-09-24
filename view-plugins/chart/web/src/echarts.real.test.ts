/**
 * The option and the event handlers against REAL ECharts (SSR mode: SVG, no
 * DOM), not a double. A hand-made `brushselected` payload once put `areas` at
 * the top of the event; ECharts puts it inside `batch[0]`, and the chart threw
 * on load in the browser while every test with the double stayed green.
 */
import { BrushComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

// The chart debounces brush events (throttleDelay 250 ms in the option).
const settle = () => new Promise((r) => setTimeout(r, 400));

import "./echarts"; // registers the chart's series + components
import { highlightTargets } from "./highlight";
import { toOption } from "./option";
import { type BrushSelected, selectionFromBrush } from "./selection";
import { answer, base, cat, f64, layer, q8 } from "./testAnswer";

echarts.use([SVGRenderer, BrushComponent]);

function chartFor(doc: object, a: ReturnType<typeof answer>) {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  const events: BrushSelected[] = [];
  chart.on("brushselected", (e) => {
    events.push(e as BrushSelected);
  });
  chart.setOption(built.option, true);
  return { chart, built, events };
}

const scatter = {
  ...base,
  mark: "scatter",
  encoding: {
    x: { field: "a", type: "quantitative" },
    y: { field: "b", type: "quantitative" },
    color: { field: "lot", type: "nominal" },
  },
};
const scatterAnswer = answer(
  layer(
    "scatter",
    4,
    { a: f64([1, 2, 3, 4]), b: f64([1, 2, 3, 4]), lot: cat(["A", "B", "A", "B"]) },
    { highlight: btoa(String.fromCharCode(0b0101)), lit: 2 },
  ),
);

describe("against real ECharts", () => {
  it("takes the option, and the setup-time brushselected selects nothing", () => {
    const { chart, built, events } = chartFor(scatter, scatterAnswer);
    for (const e of events) expect(selectionFromBrush(e, built)).toEqual([]);
    chart.dispose();
  });

  it("maps a real rect brush back to layer rows", async () => {
    const { chart, built, events } = chartFor(scatter, scatterAnswer);
    chart.dispatchAction({
      type: "brush",
      areas: [{ brushType: "rect", xAxisIndex: 0, coordRange: [[1.5, 3.5], [0, 5]] }],
    });
    await settle();
    const last = events.at(-1);
    expect(last).toBeDefined();
    // x in 1.5..3.5 → rows 1 (a=2) and 2 (a=3), whichever series they sit in.
    expect(selectionFromBrush(last as BrushSelected, built)).toEqual([{ source: "brush", layer: 0, rows: [1, 2] }]);
    chart.dispose();
  });

  it("maps a real polygon (lasso) back to layer rows", async () => {
    const { chart, built, events } = chartFor(scatter, scatterAnswer);
    chart.dispatchAction({
      type: "brush",
      areas: [{ brushType: "polygon", xAxisIndex: 0, coordRange: [[0, 0], [0, 5], [2.5, 5], [2.5, 0]] }],
    });
    await settle();
    expect(selectionFromBrush(events.at(-1) as BrushSelected, built)).toEqual([
      { source: "lasso", layer: 0, rows: [0, 1] },
    ]);
    chart.dispose();
  });

  it("hit-tests a real brush over a grid's cells", async () => {
    const grid = {
      ...base,
      mark: "grid",
      encoding: {
        x: { field: "x", type: "ordinal" },
        y: { field: "y", type: "ordinal" },
        color: { field: "v", type: "quantitative" },
      },
    };
    const a = answer(layer("grid", 4, { x: f64([0, 1, 0, 1]), y: f64([0, 0, 1, 1]), v: q8([0, 80, 160, 254], 0, 1) }));
    const { chart, built, events } = chartFor(grid, a);
    chart.dispatchAction({
      type: "brush",
      areas: [{ brushType: "rect", xAxisIndex: 0, coordRange: [[0.5, 1.5], [-0.5, 1.5]] }],
    });
    await settle();
    expect(selectionFromBrush(events.at(-1) as BrushSelected, built)).toEqual([{ source: "brush", layer: 0, rows: [1, 3] }]);
    chart.dispose();
  });

  it("accepts the highlight actions the chart dispatches", () => {
    const { chart, built } = chartFor(scatter, scatterAnswer);
    const targets = highlightTargets(built, scatterAnswer);
    expect(targets.length).toBeGreaterThan(0);
    for (const t of targets) {
      expect(() => chart.dispatchAction({ type: "highlight", seriesIndex: t.seriesIndex, dataIndex: t.dataIndex })).not.toThrow();
    }
    chart.dispose();
  });
});
