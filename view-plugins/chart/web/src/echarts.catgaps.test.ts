/**
 * #847/#848 PR 5 P36 row 11: a piece of a stack is 0 at a category where it
 * has no row -- it adds nothing to the stack there -- as off a category axis
 * (P29); on a log y it is empty only where nothing lies beneath it (P35 row 8).
 * Since P35 row 7 split a series' raw rows into pieces, an AREA stacked from
 * rows on a category x had pieces with no fillers: a piece's area ran straight
 * across the categories where it had no row, drawing area where there was no
 * data.
 *
 * Against REAL ECharts (SSR): the drawn area outlines are read from the SVG
 * and mapped back to data through the grid. The oracle is pandas:
 *
 *   df = pd.DataFrame({"c": list("pqrspspqrs"), "g": list("aaaaaabbbb"),
 *                      "v": [1, 2, 3, 4, 5, 6, 1, 1, 1, 1]})
 *   df["k"] = df.groupby(["g", "c"]).cumcount()
 *   df.pivot_table(index=["g", "k"], columns="c", values="v",
 *                  aggfunc="sum", fill_value=0).cumsum()
 *
 *        p  q  r   s
 *   a 0  1  2  3   4
 *     1  6  2  3  10     <- a's second rows, at p and s only
 *   b 0  7  3  4  11
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
const C = ["p", "q", "r", "s", "p", "s", "p", "q", "r", "s"];
const G = ["a", "a", "a", "a", "a", "a", "b", "b", "b", "b"];
const V = [1, 2, 3, 4, 5, 6, 1, 1, 1, 1];
// pandas, above: each piece's stacked top per category, in stack order
const TOPS = [
  [1, 2, 3, 4],
  [6, 2, 3, 10],
  [7, 3, 4, 11],
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
  it("draws each piece's outline at the cumulative sums, every category (red before: a's second piece ran straight over q and r)", () => {
    const { chart, svg } = draw(doc({ type: "area", stack: true }), rowsOf("area"));
    const drawn = outlines(chart, svg);
    expect(drawn).toHaveLength(3);
    expect(drawn.map(pointsOf)).toEqual(TOPS.map((t, k) => expected(t, k === 0 ? null : TOPS[k - 1])));
    chart.dispose();
  });

  it("puts every row's stacked top where pandas puts it, and adds a point that draws no row", () => {
    const { chart, built, model } = draw(doc({ type: "area", stack: true }), rowsOf("area"));
    // a's second piece: its rows at p and s, nothing at q and r
    expect(built.series[1].rows).toEqual([4, null, null, 5]);
    const top = (i: number, j: number) => {
      const data = model.getSeriesByIndex(i).getData();
      return data.get(data.getCalculationInfo("stackResultDimension"), j);
    };
    expect([0, 1, 2].map((i) => [0, 1, 2, 3].map((j) => top(i, j)))).toEqual(TOPS);
    chart.dispose();
  });

  it("on a log y, leaves a piece empty where nothing lies beneath it, and fills it with 0 over a row", () => {
    // a's first piece has no row at q: nothing beneath it there (empty); a's
    // second piece has rows at p and s: over q nothing lies beneath (empty),
    // over r a's first piece does (0). b sits on whatever is there.
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
    // categories p q r s: the first piece's q, the second's q empty; its r 0
    expect(valueAt(0, 1)?.[1]).toBeNull();
    expect(valueAt(1, 1)?.[1]).toBeNull();
    expect(valueAt(1, 2)?.[1]).toBe(0);
    const top = (i: number, j: number) => {
      const data = model.getSeriesByIndex(i).getData();
      return data.get(data.getCalculationInfo("stackResultDimension"), j);
    };
    // b: p 2+5+1, q 1 (on nothing), r 3+1, s 4+6+1
    expect([0, 1, 2, 3].map((j) => top(2, j))).toEqual([8, 1, 4, 11]);
    expect(svg).not.toContain("Infinity");
    expect(svg).not.toContain("NaN");
    // a's second piece has no vertex at q (empty there): its lone point at p
    // (2 up to 7) draws no area, and from r to s it stands on a's first
    // piece, adding nothing over r
    const drawn = outlines(chart, svg);
    expect([...pointsOf(drawn[1])].sort()).toEqual(["p:2", "p:7", "r:3", "s:10", "s:4"]);
    chart.dispose();
  });
});

describe("a bar of rows stacked on a category axis (P36 row 11)", () => {
  /** Each piece's bar per category as [from, to] in data along the value
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

  it("draws nothing for a horizontal bar's piece where it has no row, and the next piece sits on what is beneath", () => {
    const horizontal = {
      ...base,
      mark: { type: "bar", stack: true },
      encoding: { x: { field: "v", type: "quantitative" }, y: { field: "c", type: "nominal" }, color: { field: "g", type: "nominal" } },
    };
    const { chart, built, model } = draw(horizontal, rowsOf("bar"));
    expect(bars(chart, model, 3, true)).toEqual([
      [[0, 1], [0, 2], [0, 3], [0, 4]],
      [[1, 6], null, null, [4, 10]],
      [[6, 7], [2, 3], [3, 4], [10, 11]],
    ]);
    // a filler keeps the category where a horizontal bar has it: second
    const series = built.option.series as { data: ({ value?: unknown[] } | unknown[])[] }[];
    expect(series[1].data[1]).toMatchObject({ value: [0, 1] });
    chart.dispose();
  });

  it("on a log x, leaves a horizontal bar's piece empty where nothing lies beneath it", () => {
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

  it("draws nothing for a piece where it has no row, and the next piece sits on what is beneath", () => {
    const { chart, model } = draw(doc({ type: "bar", stack: true }), rowsOf("bar"));
    expect(bars(chart, model, 3)).toEqual([
      [[0, 1], [0, 2], [0, 3], [0, 4]],
      [[1, 6], null, null, [4, 10]],
      [[6, 7], [2, 3], [3, 4], [10, 11]],
    ]);
    chart.dispose();
  });
});
