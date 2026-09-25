/**
 * Review round 1 findings, each pinned where it was found:
 * - a highlight on `line` / `area` drew nothing (the dim lives on symbols,
 *   which a line hides by default);
 * - a pie's legend click selected nothing (its series has no name — its
 *   legend entries are the slices);
 * - an errorbar's rows count in the highlight summary but were never dimmed.
 */
import { describe, expect, it } from "vitest";

import { toOption } from "./option";
import { selectionFromLegend } from "./selection";
import { answer, base, cat, f64, layer, time } from "./testAnswer";

const LIT = { highlight: btoa(String.fromCharCode(0b0110)), lit: 2 };
const xy = { x: { field: "a", type: "quantitative" }, y: { field: "b", type: "quantitative" } };

describe("highlight on a line or area", () => {
  it.each(["line", "area"])("shows the points of a highlighted %s, so lit and dimmed ones differ", (mark) => {
    const a = answer(layer(mark, 4, { a: f64([1, 2, 3, 4]), b: f64([1, 2, 3, 4]) }, LIT));
    const [s] = toOption({ ...base, mark, encoding: xy }, a).option.series as { showSymbol: boolean; data: unknown[] }[];
    expect(s.showSymbol).toBe(true);
    expect(s.data[0]).toMatchObject({ itemStyle: { opacity: 0.15 } });
  });

  it("leaves a plain line's points hidden until hovered", () => {
    // #847/#848 PR 5 P25: they are there (to be hovered; see
    // echarts.hover.test.ts) but clear, and drawn only while hovered
    const a = answer(layer("line", 2, { a: f64([1, 2]), b: f64([1, 2]) }));
    type S = { showSymbol: boolean; itemStyle: { opacity: number }; emphasis: object; blur: object };
    const [s] = toOption({ ...base, mark: "line", encoding: xy }, a).option.series as S[];
    expect(s.itemStyle.opacity).toBe(0);
    // drawn while hovered, and hovering still brings this series forward
    expect(s.emphasis).toEqual({ focus: "self", itemStyle: { opacity: 1 } });
    // while one is hovered the others are blurred, not drawn: at the blur's
    // 0.15 every other point showed as a faint ring (seen in Chromium)
    expect(s.blur).toEqual({ itemStyle: { opacity: 0 }, lineStyle: { opacity: 0.15 } });
  });

  it("keeps a highlighted line's points hidden when it asks for none (P25)", () => {
    const a = answer(layer("line", 4, { a: f64([1, 2, 3, 4]), b: f64([1, 2, 3, 4]) }, LIT));
    const [s] = toOption({ ...base, mark: { type: "line", point: false }, encoding: xy }, a).option.series as {
      itemStyle: { opacity: number };
      data: unknown[];
    }[];
    expect(s.itemStyle.opacity).toBe(0);
    // no point carries an opacity of its own that would draw it
    expect(s.data.every((d) => !(d && typeof d === "object" && "itemStyle" in d))).toBe(true);
  });

  it("draws a highlighted line's points, lit and dimmed (P25 leaves them drawn)", () => {
    const a = answer(layer("line", 4, { a: f64([1, 2, 3, 4]), b: f64([1, 2, 3, 4]) }, LIT));
    const [s] = toOption({ ...base, mark: "line", encoding: xy }, a).option.series as { itemStyle?: { opacity?: number } }[];
    expect(s.itemStyle?.opacity).toBeUndefined();
  });
});

describe("a pie's legend", () => {
  it("selects the rows of the slices still shown", () => {
    const pie = {
      ...base,
      mark: "pie",
      encoding: { theta: { field: "v", type: "quantitative" }, color: { field: "k", type: "nominal" } },
    };
    const built = toOption(pie, answer(layer("pie", 3, { k: cat(["a", "b", "c"]), v: f64([1, 2, 3]) })));
    expect(selectionFromLegend({ a: true, b: false, c: true }, built)).toEqual([
      { source: "legend", layer: 0, rows: [0, 2] },
    ]);
  });
});

describe("a rule on a category axis", () => {
  // ECharts reads a number on a category axis as an INDEX: a rule at the
  // category 2022 of [2021, 2022, 2023] belongs at index 1, not 2022.
  const bars = {
    mark: "bar",
    encoding: { x: { field: "year", type: "ordinal" }, y: { field: "v", type: "quantitative" } },
  };
  const barAnswer = layer("bar", 3, { year: cat([2021, 2022, 2023]), v: f64([1, 2, 3]) });
  const markLine = (rule: unknown, cols: Parameters<typeof layer>[2], rows = 0) =>
    (
      toOption({ ...base, layer: [bars, rule] }, answer(barAnswer, layer("rule", rows, cols))).option.series as {
        markLine: { data: unknown };
      }[]
    )[1].markLine.data;

  it("places a datum at its category", () => {
    expect(markLine({ mark: "rule", encoding: { x: { datum: 2022 } } }, {})).toMatchObject([{ xAxis: 1, name: undefined }]);
  });

  it("places a field's values at their categories", () => {
    const rule = { mark: "rule", encoding: { x: { field: "year", type: "ordinal" } } };
    const data = markLine(rule, { year: cat([2023]) }, 1) as { xAxis: number; label: { formatter: () => string } }[];
    expect(data).toMatchObject([{ xAxis: 2 }]);
    // labelled with its category, not its index (P26)
    expect(data[0].label.formatter()).toBe("2023");
  });
});

describe("selectionValues for a key a channel sends as numbers or time", () => {
  it("reads the key's marking strings, not the channel's encoding", async () => {
    const { selectionValues } = await import("./selection");
    const a = answer(
      layer("line", 2, {
        day: time(["2024-01-01", "2024-01-02"]),
        "$key.day": cat(["2024-01-01", "2024-01-02"]),
      }),
    );
    expect(selectionValues({ source: "brush", layer: 0, rows: [1] }, a, ["day"], [])).toEqual({ day: ["2024-01-02"] });
  });
});

describe("a highlighted errorbar", () => {
  it("dims its unlit rows", () => {
    const spec = {
      ...base,
      mark: "errorbar",
      encoding: { x: { field: "g", type: "nominal" }, y: { field: "v", type: "quantitative" } },
    };
    const a = answer(
      layer(
        "errorbar",
        4,
        { g: cat(["a", "b", "c", "d"]), $lo: f64([1, 1, 1, 1]), $mid: f64([2, 2, 2, 2]), $hi: f64([3, 3, 3, 3]) },
        LIT,
      ),
    );
    const s = (toOption(spec, a).option.series as { renderItem: Function }[])[0];
    const draw = (row: number) =>
      (s.renderItem as (p: unknown, api: unknown) => { children: { style: { opacity: number } }[] })(
        { dataIndex: row },
        { coord: (p: number[]) => p, value: (d: number) => [row, 1, 3][d], style: () => ({}) },
      ).children[0].style.opacity;
    expect([draw(0), draw(1), draw(2), draw(3)]).toEqual([0.15, 1, 1, 0.15]);
  });
});
