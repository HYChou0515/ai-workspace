/**
 * #847/#848 PR 5 P36 row 11: a series of a stack is 0 at a category where it
 * has no row -- it adds nothing to the stack there -- as off a category axis
 * (P29); on a log y it is empty only where nothing lies beneath it (P35 row 8).
 * (Found when P35 row 7 split a series' raw rows into pieces: an AREA stacked
 * from rows on a category x ran straight across the categories where a piece
 * had no row. P37 row 12 draws a series' rows at a category as one point,
 * their sum, so here a series is one area, and the rule is kept for the
 * category a series has no row at.)
 *
 * Against REAL ECharts (SSR): the drawn area outlines are read from the SVG
 * and mapped back to data through the grid. The oracle is pandas:
 *
 *   df = pd.DataFrame({"c": list("prspspqrs"), "g": list("aaaaabbbb"),
 *                      "v": [1, 3, 4, 5, 6, 1, 1, 1, 1]})
 *   df.pivot_table(index="g", columns="c", values="v", aggfunc="sum",
 *                  fill_value=0).cumsum()
 *
 *        p  q  r   s
 *   a    6  0  3  10     <- a has no row at q; two rows at p and at s
 *   b    7  1  4  11
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { type Answer, toOption } from "./option";
import { answer, base, cat, f64, layer } from "./testAnswer";

echarts.use([SVGRenderer]);

type Layout = { x: number; y: number; width: number; height: number };
type Model = {
  getSeriesByIndex(i: number): {
    getData(): {
      count(): number;
      get(dim: string, i: number): number;
      getCalculationInfo(key: string): string;
      getItemLayout(i: number): Layout | null;
    };
  };
};

function draw(doc: object, a: Answer) {
  const built = toOption(doc, a);
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 600, height: 400 });
  chart.setOption(built.option, true);
  const model = (chart as unknown as { getModel(): Model }).getModel();
  return { chart, built, model, svg: chart.renderToSVGString() };
}

const CATS = ["p", "q", "r", "s"];
const C = ["p", "r", "s", "p", "s", "p", "q", "r", "s"];
const G = ["a", "a", "a", "a", "a", "b", "b", "b", "b"];
const V = [1, 3, 4, 5, 6, 1, 1, 1, 1];
// pandas, above: each series' stacked top per category, in stack order
const TOPS = [
  [6, 0, 3, 10],
  [7, 1, 4, 11],
];
const rowsOf = (mark: string) => answer(layer(mark, C.length, { c: cat(C), g: cat(G), v: f64(V) }));
const doc = (mark: object, y: object = { field: "v", type: "quantitative" }) => ({
  ...base,
  mark,
  encoding: { x: { field: "c", type: "nominal" }, y, color: { field: "g", type: "nominal" } },
});

/** The vertices of every filled area in the SVG, in data coordinates
 * ([category index, y], rounded), one list per area path, in drawing order. */
function outlines(chart: echarts.ECharts, svg: string): [number, number][][] {
  return [...svg.matchAll(/<path d="([^"]*)"[^>]*fill-opacity="0.7"/g)].map(([, d]) =>
    d
      .replace(/Z/g, "")
      .split(/[ML]/)
      .map((p) => p.trim())
      .filter(Boolean)
      .map((p) => {
        const [px, py] = p.split(/\s+/).map(Number);
        const [x, y] = chart.convertFromPixel({ gridIndex: 0 }, [px, py]) as number[];
        return [Math.round(x), Math.round(y * 100) / 100] as [number, number];
      }),
  );
}

/** One area's outline as "category:y" points, a set: the top line over every
 * category it is drawn at, and the line it stands on. */
const pointsOf = (outline: [number, number][]) => new Set(outline.map(([x, y]) => `${CATS[x]}:${y}`));
const expected = (tops: number[], under: number[] | null) =>
  new Set([...CATS.map((c, i) => `${c}:${tops[i]}`), ...CATS.map((c, i) => `${c}:${under ? under[i] : 0}`)]);

describe("an area of rows stacked on a category x (P36 row 11)", () => {
  it("draws each series' outline at the cumulative sums, every category, 0 where it has no row", () => {
    const { chart, svg } = draw(doc({ type: "area", stack: true }), rowsOf("area"));
    const drawn = outlines(chart, svg);
    expect(drawn).toHaveLength(2);
    expect(drawn.map(pointsOf)).toEqual(TOPS.map((t, k) => expected(t, k === 0 ? null : TOPS[k - 1])));
    chart.dispose();
  });

  it("puts every row's stacked top where pandas puts it, and adds a point that draws no row", () => {
    const { chart, built, model } = draw(doc({ type: "area", stack: true }), rowsOf("area"));
    // a: rows 0 and 3 at p (summed), nothing at q, row 1 at r, 2 and 4 at s
    expect(built.series[0].rows).toEqual([[0, 3], null, 1, [2, 4]]);
    const top = (i: number, j: number) => {
      const data = model.getSeriesByIndex(i).getData();
      return data.get(data.getCalculationInfo("stackResultDimension"), j);
    };
    expect([0, 1].map((i) => [0, 1, 2, 3].map((j) => top(i, j)))).toEqual(TOPS);
    chart.dispose();
  });

  it("on a log y, leaves a series empty where nothing lies beneath it", () => {
    // a has no row at q: nothing beneath it there (empty). b sits on whatever
    // is there: a's sums at p (2 + 5), r, s (4 + 6), nothing at q.
    const LC = ["p", "r", "s", "p", "s", "p", "q", "r", "s"];
    const LG = ["a", "a", "a", "a", "a", "b", "b", "b", "b"];
    const LV = [2, 3, 4, 5, 6, 1, 1, 1, 1];
    const a = answer(layer("area", LC.length, { c: cat(LC), g: cat(LG), v: f64(LV) }));
    const { chart, built, model, svg } = draw(
      doc({ type: "area", stack: true }, { field: "v", type: "quantitative", scale: { type: "log" } }),
      a,
    );
    const series = built.option.series as { data: ({ value?: unknown[] } | unknown[])[] }[];
    const valueAt = (i: number, j: number) => {
      const d = series[i].data[j];
      return Array.isArray(d) ? d : d.value;
    };
    // categories p q r s: a's q empty
    expect(valueAt(0, 1)?.[1]).toBeNull();
    const top = (i: number, j: number) => {
      const data = model.getSeriesByIndex(i).getData();
      return data.get(data.getCalculationInfo("stackResultDimension"), j);
    };
    // b: p 2+5+1, q 1 (on nothing), r 3+1, s 4+6+1
    expect([0, 1, 2, 3].map((j) => top(1, j))).toEqual([8, 1, 4, 11]);
    expect(svg).not.toContain("Infinity");
    expect(svg).not.toContain("NaN");
    // a has no vertex at q (empty there): its lone point at p is a sliver of
    // no width; from r to s it is drawn from the axis's foot (1, on a log
    // axis) up to 3 and 10. b is drawn on a's sums, and on nothing at q.
    const drawn = outlines(chart, svg);
    expect(drawn.map((d) => [...pointsOf(d)].sort())).toEqual([
      ["p:1", "p:7", "r:1", "r:3", "s:1", "s:10"],
      ["p:7", "p:8", "q:1", "r:3", "r:4", "s:10", "s:11"],
    ]);
    chart.dispose();
  });
});

describe("a bar of rows stacked on a category axis (P36 row 11)", () => {
  /** Each series' bar per category as [from, to] in data along the value
   * axis, or null where it draws nothing (no bar, or one of no length). A
   * horizontal bar's category is its y. */
  function bars(chart: echarts.ECharts, model: Model, n: number, horizontal = false): ([number, number] | null)[][] {
    return Array.from({ length: n }, (_, i) => {
      const data = model.getSeriesByIndex(i).getData();
      const out: ([number, number] | null)[] = CATS.map(() => null);
      for (let j = 0; j < data.count(); j++) {
        const l = data.getItemLayout(j);
        if (!l || Math.abs(horizontal ? l.width : l.height) < 0.5) continue;
        const a = chart.convertFromPixel({ gridIndex: 0 }, [l.x, l.y]) as number[];
        const b = chart.convertFromPixel({ gridIndex: 0 }, [l.x + l.width, l.y + l.height]) as number[];
        const [v, c] = horizontal ? [0, 1] : [1, 0];
        const [lo, hi] = [Math.min(a[v], b[v]), Math.max(a[v], b[v])].map((x) => Math.round(x * 100) / 100);
        out[Math.round((a[c] + b[c]) / 2)] = [lo, hi];
      }
      return out;
    });
  }

  it("draws nothing for a horizontal bar's series where it has no row, and the next series sits on what is beneath", () => {
    const horizontal = {
      ...base,
      mark: { type: "bar", stack: true },
      encoding: { x: { field: "v", type: "quantitative" }, y: { field: "c", type: "nominal" }, color: { field: "g", type: "nominal" } },
    };
    const { chart, built, model } = draw(horizontal, rowsOf("bar"));
    expect(bars(chart, model, 2, true)).toEqual([
      [[0, 6], null, [0, 3], [0, 10]],
      [[6, 7], [0, 1], [3, 4], [10, 11]],
    ]);
    // a filler keeps the category where a horizontal bar has it: second
    const series = built.option.series as { data: ({ value?: unknown[] } | unknown[])[] }[];
    expect(series[0].data[1]).toMatchObject({ value: [0, 1] });
    chart.dispose();
  });

  it("on a log x, leaves a horizontal bar's series empty where nothing lies beneath it", () => {
    const horizontal = {
      ...base,
      mark: { type: "bar", stack: true },
      encoding: {
        x: { field: "v", type: "quantitative", scale: { type: "log" } },
        y: { field: "c", type: "nominal" },
        color: { field: "g", type: "nominal" },
      },
    };
    // a has no row at q; b has one at every category
    const a = answer(layer("bar", 7, { c: cat(["p", "r", "s", "p", "q", "r", "s"]), g: cat(["a", "a", "a", "b", "b", "b", "b"]), v: f64([2, 3, 4, 1, 1, 1, 1]) }));
    const { chart, built, svg } = draw(horizontal, a);
    const series = built.option.series as { data: ({ value?: unknown[] } | unknown[])[] }[];
    expect(series[0].data[1]).toMatchObject({ value: [null, 1] });
    expect(svg).not.toContain("Infinity");
    expect(svg).not.toContain("NaN");
    chart.dispose();
  });

  it("draws nothing for a series where it has no row, and the next series sits on what is beneath", () => {
    const { chart, model } = draw(doc({ type: "bar", stack: true }), rowsOf("bar"));
    expect(bars(chart, model, 2)).toEqual([
      [[0, 6], null, [0, 3], [0, 10]],
      [[6, 7], [0, 1], [3, 4], [10, 11]],
    ]);
    chart.dispose();
  });
});
