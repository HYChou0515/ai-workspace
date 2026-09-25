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

  it("leaves a plain line's points hidden", () => {
    const a = answer(layer("line", 2, { a: f64([1, 2]), b: f64([1, 2]) }));
    const [s] = toOption({ ...base, mark: "line", encoding: xy }, a).option.series as { showSymbol: boolean }[];
    expect(s.showSymbol).toBe(false);
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
    expect(markLine({ mark: "rule", encoding: { x: { datum: 2022 } } }, {})).toEqual([{ xAxis: 1, name: undefined }]);
  });

  it("places a field's values at their categories", () => {
    const rule = { mark: "rule", encoding: { x: { field: "year", type: "ordinal" } } };
    expect(markLine(rule, { year: cat([2023]) }, 1)).toEqual([{ xAxis: 2 }]);
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
    expect(selectionValues({ source: "brush", layer: 0, rows: [1] }, a, ["day"])).toEqual({ day: ["2024-01-02"] });
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
