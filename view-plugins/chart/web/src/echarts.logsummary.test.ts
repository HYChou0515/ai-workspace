/**
 * #847/#848 PR 5 P40 row 20: every value a log axis draws goes through the
 * axis's `at` -- an errorbar's ends (`y`/`y2`, or `$lo`/`$hi`), a boxplot's
 * five summaries and its outliers too -- and the note counts exactly what
 * `at` left out. Before, P38's note counted an errorbar's ends at or below 0
 * while they were still sent to ECharts, which drew Infinity / NaN; a
 * boxplot's summaries bypassed `at` altogether.
 *
 * Against REAL ECharts (SSR): the SVG is read for Infinity / NaN, and the
 * errorbar's strokes are counted.
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { type Answer, toOption } from "./option";
import { answer, base, cat, f64, layer } from "./testAnswer";

echarts.use([SVGRenderer]);

const LOG = { type: "quantitative", scale: { type: "log" } };
const X = { field: "x", type: "nominal" };

function render(doc: object, a: Answer) {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const svg = chart.renderToSVGString();
  chart.dispose();
  return { built, svg };
}

/** The errorbar's strokes (stems and caps), by their colour. */
const strokes = (svg: string) => svg.split("\n").filter((l) => l.includes('stroke="#555"'));
const logNotes = (notes: string[]) => notes.filter((n) => n.includes("log"));

describe("an errorbar on a log y (P40 row 20)", () => {
  const doc = { ...base, mark: "errorbar", encoding: { x: X, y: { field: "lo", ...LOG }, y2: { field: "hi" } } };
  const bars = () => answer(layer("errorbar", 3, { x: cat(["a", "b", "c"]), lo: f64([-1, 0, 2]), hi: f64([5, 6, 7]) }));

  it("sends no end at or below 0 to ECharts, and draws no Infinity or NaN (red before)", () => {
    const { built, svg } = render(doc, bars());
    expect((built.option.series as { data: unknown[] }[])[0].data).toEqual([
      [0, null, 5],
      [1, null, 6],
      [2, 2, 7],
    ]);
    expect(svg).not.toMatch(/Infinity|NaN/);
  });

  it("counts exactly the ends it left out", () => {
    expect(logNotes(render(doc, bars()).built.notes)).toEqual(["2 values at or below 0 not drawn on the log y axis"]);
  });

  it("draws what has a place: a bar whose low end has none runs off the plot's foot, capped at its high end only", () => {
    // a and b: a stem and the high cap; c: a stem and both caps
    expect(strokes(render(doc, bars()).svg)).toHaveLength(2 + 2 + 3);
  });

  it("runs that stem to the plot's foot, below every value drawn", () => {
    const built = toOption(doc, bars());
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
    chart.setOption(built.option, true);
    const grid = (chart as unknown as { getModel(): { getComponent(m: string): { coordinateSystem: { getRect(): { y: number; height: number } } } } })
      .getModel()
      .getComponent("grid").coordinateSystem.getRect();
    // the stems are the strokes with one x; a's is the first
    const stems = strokes(chart.renderToSVGString()).flatMap((l) => {
      const m = /d="M([\d.]+) ([\d.]+)L([\d.]+) ([\d.]+)"/.exec(l);
      return m && m[1] === m[3] ? [[Number(m[2]), Number(m[4])]] : [];
    });
    chart.dispose();
    expect(stems).toHaveLength(3);
    expect(Math.max(...stems[0])).toBeCloseTo(grid.y + grid.height, 0);
  });

  it("draws nothing of a bar with no end in place, and counts both ends", () => {
    const a = answer(layer("errorbar", 2, { x: cat(["a", "b"]), lo: f64([-3, 1]), hi: f64([0, 2]) }));
    const { built, svg } = render(doc, a);
    expect(svg).not.toMatch(/Infinity|NaN/);
    expect(strokes(svg)).toHaveLength(3);
    expect(logNotes(built.notes)).toEqual(["2 values at or below 0 not drawn on the log y axis"]);
  });

  it("does the same with the summaries it computes ($lo / $hi)", () => {
    const summary = { ...base, mark: "errorbar", encoding: { x: X, y: { field: "v", ...LOG } } };
    const a = answer(layer("errorbar", 2, { x: cat(["a", "b"]), $lo: f64([-1, 1]), $mid: f64([2, 2]), $hi: f64([4, 3]) }));
    const { built, svg } = render(summary, a);
    expect(svg).not.toMatch(/Infinity|NaN/);
    expect(logNotes(built.notes)).toEqual(["1 value at or below 0 not drawn on the log y axis"]);
    expect(strokes(svg)).toHaveLength(2 + 3);
  });

  it("(control) on a linear y every end is drawn and nothing is noted", () => {
    const linear = { ...base, mark: "errorbar", encoding: { x: X, y: { field: "lo", type: "quantitative" }, y2: { field: "hi" } } };
    const { built, svg } = render(linear, bars());
    expect(strokes(svg)).toHaveLength(9);
    expect(built.notes).toEqual([]);
  });
});

describe("a boxplot on a log y (P40 row 20)", () => {
  const doc = { ...base, mark: "boxplot", encoding: { x: X, y: { field: "v", ...LOG } } };
  const box = (lo: number[], q1: number[], outliers?: number[]) =>
    answer(
      layer("boxplot", 3, {
        x: cat(["a", "b", "c"]),
        $lo: f64(lo),
        $q1: f64(q1),
        $mid: f64([3, 3, 3]),
        $q3: f64([4, 4, 4]),
        $hi: f64([5, 5, 5]),
      }, outliers ? { outliers: { rows: outliers.length, columns: { x: cat(outliers.map(() => "a")), v: f64(outliers) } } } : {}),
    );

  it("sends no summary at or below 0 to ECharts, and counts each one it left out (red before)", () => {
    const { built, svg } = render(doc, box([0, -2, 1], [2, 0, 2]));
    const data = (built.option.series as { data: unknown[][] }[])[0].data;
    expect(data.map((d) => d.slice(1))).toEqual([
      [null, 2, 3, 4, 5],
      [null, null, 3, 4, 5],
      [1, 2, 3, 4, 5],
    ]);
    expect(svg).not.toMatch(/Infinity|NaN/);
    expect(logNotes(built.notes)).toContain("3 values at or below 0 not drawn on the log y axis");
  });

  it("says which boxes it could not draw: ECharts draws no box with a summary missing", () => {
    const { built } = render(doc, box([0, -2, 1], [2, 0, 2]));
    expect(logNotes(built.notes)).toContain("2 boxes with a part at or below 0 not drawn on the log y axis");
  });

  it("leaves out an outlier at or below 0 and counts it", () => {
    const { built, svg } = render(doc, box([1, 1, 1], [2, 2, 2], [-4, 0, 9]));
    const outliers = (built.option.series as { data: unknown[][] }[])[1].data;
    expect(outliers.map((d) => d[1])).toEqual([null, null, 9]);
    expect(svg).not.toMatch(/Infinity|NaN/);
    expect(logNotes(built.notes)).toEqual(["2 values at or below 0 not drawn on the log y axis"]);
  });

  it("does not count a box whose summary is missing (not at or below 0)", () => {
    const a = answer(
      layer("boxplot", 1, { x: cat(["a"]), $lo: f64([null]), $q1: f64([2]), $mid: f64([3]), $q3: f64([4]), $hi: f64([5]) }),
    );
    expect(logNotes(render(doc, a).built.notes)).toEqual([]);
  });

  it("(control) with every summary positive, notes nothing", () => {
    expect(logNotes(render(doc, box([1, 1, 1], [2, 2, 2])).built.notes)).toEqual([]);
  });
});

describe("a layer marked stack that is not drawn as a stack (P40 row 20)", () => {
  // Only a bar or an area is stacked; a line's `stack: true` stacks nothing,
  // so its 0 is a line's 0: no place on a log axis, left out and counted.
  it("leaves a line's 0 out on a log y and counts it (red before: sent as 0, noted nowhere)", () => {
    const doc = {
      ...base,
      mark: { type: "line", stack: true },
      encoding: { x: { field: "x", type: "quantitative" }, y: { field: "y", ...LOG } },
    };
    const { built, svg } = render(doc, answer(layer("line", 3, { x: f64([1, 2, 3]), y: f64([10, 0, 100]) })));
    expect(svg).not.toMatch(/Infinity|NaN/);
    expect(logNotes(built.notes)).toEqual(["1 value at or below 0 not drawn on the log y axis"]);
  });
});
