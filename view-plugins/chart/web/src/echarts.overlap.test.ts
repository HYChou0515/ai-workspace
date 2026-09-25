/**
 * #847/#848 PR 5 P24 against REAL ECharts (SSR, SVG): no axis draws labels
 * that run into each other. Seen at 390 px wide: a scatter's number axis read
 * "98100102104106" in one run. P14 left overlapping labels out on a time axis
 * only; a number, log and grid axis do now too, on both axes, and a category
 * axis already did by ECharts' own interval (pinned here).
 */
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { describe, expect, it } from "vitest";

import "./echarts"; // registers the chart's series + components
import { toOption } from "./option";
import { answer, base, cat, f64, layer, q8, time } from "./testAnswer";
import type { WireColumn } from "./wire";

echarts.use([SVGRenderer]);

type Label = { x: number; y: number; text: string };

/** Every text ECharts draws, with where it is anchored. `force` overrides the
 * chart's own x axis labels (the control). */
function draw(
  doc: object,
  columns: Record<string, WireColumn>,
  rows: number,
  width: number,
  height: number,
  force?: Record<string, unknown>,
  mark = "scatter",
): Label[] {
  const built = toOption(doc, answer(layer(mark, rows, columns)), {
    gridImage: () => ({}) as unknown as HTMLCanvasElement,
  });
  const option = built.option as { xAxis: Record<string, unknown>[] };
  if (force) {
    const [x] = option.xAxis;
    option.xAxis = [{ ...x, axisLabel: { ...(x.axisLabel as object), ...force } }];
  }
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height });
  chart.setOption(option, true);
  const svg = chart.renderToSVGString();
  chart.dispose();
  return [...svg.matchAll(/<text[^>]*transform="translate\(([-\d.]+) ([-\d.]+)\)"[^>]*>([^<]*)<\/text>/g)].map((m) => ({
    x: Number(m[1]),
    y: Number(m[2]),
    text: m[3],
  }));
}

/** The labels on one axis: the x axis's share one row, the y axis's one column. */
function axisLabels(labels: Label[], which: "x" | "y", name: string, only = /./): Label[] {
  const key = (l: Label) => String(which === "x" ? l.y : l.x);
  const groups = new Map<string, Label[]>();
  for (const l of labels) if (l.text !== name && only.test(l.text)) groups.set(key(l), [...(groups.get(key(l)) ?? []), l]);
  return [...groups.values()].sort((a, b) => b.length - a.length)[0];
}

/** Centred labels at 12 px take ~6.5 px a character, and side by side need a
 * gap to read as two ("98" and "102" touching read "98102", seen at 390 px);
 * stacked ones a 12 px line (the font size). */
const GAP = 4;
function expectApart(row: Label[], which: "x" | "y") {
  const sorted = [...row].sort((a, b) => (which === "x" ? a.x - b.x : a.y - b.y));
  for (let i = 1; i < sorted.length; i++) {
    const [a, b] = [sorted[i - 1], sorted[i]];
    if (which === "x") {
      const room = ((a.text.length + b.text.length) / 2) * 6.5 + GAP;
      expect(b.x - a.x, `${a.text} | ${b.text}`).toBeGreaterThanOrEqual(room);
    } else {
      expect(b.y - a.y, `${a.text} | ${b.text}`).toBeGreaterThanOrEqual(12);
    }
  }
}

const N = 40;
const spread = (lo: number, hi: number) => Array.from({ length: N }, (_, i) => lo + ((hi - lo) * i) / (N - 1));
const doc = (x: object, y: object = { field: "v", type: "quantitative" }) => ({
  ...base,
  mark: "scatter",
  encoding: { x: { field: "x", ...x }, y },
});

describe("an axis in a narrow chart, against real ECharts", () => {
  it("(control) the same chart with every label kept runs them together, so the check can see it", () => {
    const labels = draw(doc({ type: "quantitative" }), { x: f64(spread(98, 106)), v: f64(spread(0, 1)) }, N, 150, 300, {
      hideOverlap: false,
    });
    const sorted = axisLabels(labels, "x", "x").sort((a, b) => a.x - b.x);
    const touching = sorted.some(
      (b, i) => i > 0 && b.x - sorted[i - 1].x < ((b.text.length + sorted[i - 1].text.length) / 2) * 6.5,
    );
    expect(touching).toBe(true);
  });

  it("leaves out number labels that would overlap (the 390 px scatter)", () => {
    const labels = draw(doc({ type: "quantitative" }), { x: f64(spread(98, 106)), v: f64(spread(0, 1)) }, N, 150, 300);
    const row = axisLabels(labels, "x", "x");
    expect(row.length).toBeGreaterThan(1);
    expectApart(row, "x");
  });

  it("leaves out log-scale labels that would overlap", () => {
    const labels = draw(
      doc({ type: "quantitative", scale: { type: "log" } }),
      { x: f64(spread(0, 8).map((e) => 10 ** e)), v: f64(spread(0, 1)) },
      N,
      150,
      300,
    );
    const row = axisLabels(labels, "x", "x");
    expect(row.length).toBeGreaterThan(1);
    expectApart(row, "x");
  });

  // Category axes are pins: ECharts' own label interval for a category axis
  // leaves these out, and adding `hideOverlap` to it changed no label drawn.
  it("leaves out category labels that would overlap", () => {
    const names = Array.from({ length: N }, (_, i) => `L${String(i + 1).padStart(2, "0")}`);
    const labels = draw(doc({ type: "nominal" }), { x: cat(names), v: f64(spread(0, 1)) }, N, 150, 300);
    const row = axisLabels(labels, "x", "x");
    expect(row.length).toBeGreaterThan(1);
    expectApart(row, "x");
  });

  it("leaves out category labels that would overlap when their lengths differ", () => {
    // a long label every seventh: still apart
    const names = Array.from({ length: N }, (_, i) => (i % 7 === 3 ? `level-with-a-long-name-${i}` : `c${i}`));
    const labels = draw(doc({ type: "nominal" }), { x: cat(names), v: f64(spread(0, 1)) }, N, 300, 300);
    const row = axisLabels(labels, "x", "x");
    expect(row.length).toBeGreaterThan(1);
    expectApart(row, "x");
  });

  it("leaves out time labels that would overlap", () => {
    const days = Array.from({ length: N }, (_, i) => new Date(Date.UTC(2026, 2, 1) + i * 86_400_000).toISOString());
    const labels = draw(doc({ type: "temporal" }), { x: time(days), v: f64(spread(0, 1)) }, N, 150, 300);
    const row = axisLabels(labels, "x", "x");
    expect(row.length).toBeGreaterThan(1);
    expectApart(row, "x");
  });

  it("leaves out a grid's cell labels that would overlap", () => {
    // 40 columns of cells, each labelled at its centre, in a 300 px chart (at
    // 150 px the axis keeps one label, which says nothing about overlap)
    const xs = Array.from({ length: N }, (_, i) => 1000 + i);
    const labels = draw(
      {
        ...base,
        mark: "grid",
        encoding: {
          x: { field: "x", type: "ordinal" },
          y: { field: "y", type: "ordinal" },
          color: { field: "v", type: "quantitative" },
        },
      },
      { x: f64(xs), y: f64(xs.map(() => 0)), v: q8(xs.map((_, i) => i), 0, 1) },
      N,
      300,
      300,
      undefined,
      "grid",
    );
    // (the colour legend's end labels are not the axis's)
    const row = axisLabels(labels, "x", "x", /^10\d\d$/);
    expect(row.length).toBeGreaterThan(1);
    expectApart(row, "x");
  });

  it("leaves out a y axis's labels that would overlap in a short chart", () => {
    const labels = draw(
      doc({ type: "quantitative" }, { field: "v", type: "quantitative" }),
      { x: f64(spread(0, 1)), v: f64(spread(98, 106)) },
      N,
      400,
      110,
    );
    const column = axisLabels(labels, "y", "v");
    expect(column.length).toBeGreaterThan(1);
    expectApart(column, "y");
  });
});
