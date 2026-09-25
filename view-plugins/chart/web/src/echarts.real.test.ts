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
import { toOption } from "./option";
import { type BrushSelected, selectionFromBrush } from "./selection";
import { answer, base, cat, f64, layer, q8, time } from "./testAnswer";
import type { WireColumn } from "./wire";

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

  it("draws a highlight's unlit rows dimmed, as ECharts itself resolves the style", () => {
    // Rows 0 and 2 are lit (0b0101): lot A's two points; lot B's are not.
    const { chart } = chartFor(scatter, scatterAnswer);
    const opacity = (series: number, i: number) => {
      const model = (chart as unknown as { getModel(): { getSeriesByIndex(n: number): { getData(): { getItemVisual(i: number, k: string): { opacity?: number } } } } }).getModel();
      return model.getSeriesByIndex(series).getData().getItemVisual(i, "style").opacity ?? 1;
    };
    // Lit keep the series default (a scatter is 0.8 in ECharts); unlit are dimmed.
    expect([opacity(0, 0), opacity(0, 1)]).toEqual([0.8, 0.8]);
    expect([opacity(1, 0), opacity(1, 1)]).toEqual([0.15, 0.15]);
    chart.dispose();
  });
});

describe("a rule the axis cannot place", () => {
  // Review round 5: a datum the axis does not show became `{xAxis: null}`, and
  // ECharts threw on it ("reading 'coord'") — the whole chart broke, not the rule.
  it.each([
    ["a label the axis lacks", "nominal", "zzz"],
    ["a date past a temporal grid", "temporal", "2030-01-01"],
  ])("%s: the chart draws, without the rule, and says so", (_name, type, datum) => {
    const grid = type === "temporal";
    const doc = {
      ...base,
      layer: [
        grid
          ? {
              mark: "grid",
              encoding: { x: { field: "t", type }, y: { field: "g", type: "ordinal" }, color: { field: "v", type: "quantitative" } },
            }
          : { mark: "bar", encoding: { x: { field: "g", type }, y: { field: "v", type: "quantitative" } } },
        { mark: "rule", encoding: { x: { datum } } },
      ],
    };
    const columns: Record<string, WireColumn> = grid
      ? { t: time(["2024-03-01", "2024-03-02"]), g: cat(["a", "b"]), v: q8([0, 255], 1, 2) }
      : { g: cat(["a", "b"]), v: f64([1, 2]) };
    const { chart, built } = chartFor(doc, answer(layer(grid ? "grid" : "bar", 2, columns), layer("rule", 0, {})));
    expect(chart.renderToSVGString()).toContain("<svg");
    expect(built.notes).toContain(`rule at ${JSON.stringify(datum)} is off the x axis — not drawn`);
    chart.dispose();
  });
});
