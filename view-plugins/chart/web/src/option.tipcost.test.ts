/**
 * #847/#848 PR 5 P24: a time column's tooltip writes every time to the finest
 * part the column has, which means reading the whole column. It is read once
 * per chart, not once per hover (a hover redraws the tooltip on every mouse
 * move, over a column that can hold every point of a large scatter); and a
 * gallery reads a facet column's keys once for all its labels.
 */
import { describe, expect, it, vi } from "vitest";

import type * as ClockModule from "./clock";

const reads = vi.hoisted(() => ({ n: 0 }));
vi.mock("./clock", async (original) => {
  const real = await original<typeof ClockModule>();
  return {
    ...real,
    clockFor: (zone: string | undefined) => {
      const clock = real.clockFor(zone);
      return {
        ...clock,
        precision: (instants: Iterable<number>) => {
          reads.n += 1;
          return clock.precision(instants);
        },
      };
    },
  };
});

import { groupLabel, type FacetIndex } from "./gallery";
import { toOption } from "./option";
import { answer, base, f64, layer } from "./testAnswer";

type Tip = { formatter: (p: { seriesIndex: number; dataIndex: number }) => string };

describe("a time column's tooltip", () => {
  it("reads the column's precision once, however often it is shown", () => {
    const hours = [0, 1, 2].map((h) => Date.parse(`2026-03-01T0${h}:00:00Z`));
    const doc = {
      ...base,
      mark: "scatter",
      encoding: { x: { field: "at", type: "temporal" }, y: { field: "v", type: "quantitative" } },
    };
    const a = answer(layer("scatter", 3, { at: { ...(f64(hours) as { data: string }), kind: "time" }, v: f64([1, 2, 3]) }));
    const tip = toOption(doc, a).option.tooltip as Tip;
    const before = reads.n;
    for (const i of [0, 1, 2, 0, 1, 2]) expect(tip.formatter({ seriesIndex: 0, dataIndex: i })).toContain(":00</b>");
    expect(reads.n - before).toBe(1);
  });
});

describe("a gallery's group labels", () => {
  it("read a zoned facet column's precision once, for every label", () => {
    const index: FacetIndex = {
      build: "b",
      scale: { kind: "continuous", lo: 0, hi: 1 },
      facet: ["at"],
      cells: 1,
      layout: { x: [], y: [] },
      zones: { at: "Asia/Taipei" },
      groups: [0, 6, 12].map((h) => ({ key: [`2026-03-01 ${String(h).padStart(2, "0")}:00:00+08:00`], sort: {} })),
    };
    const before = reads.n;
    const labels = [0, 1, 2, 0, 1, 2].map((i) => groupLabel(index, i));
    expect(labels[0]).toBe("2026-03-01 00:00 Asia/Taipei");
    expect(reads.n - before).toBe(1);
  });
});
