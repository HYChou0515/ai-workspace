/**
 * Review round 2 findings on the renderer:
 * - a rule's date datum was parsed in the VIEWER's time zone (Date.parse on a
 *   zone-less string), while the sandbox reads the same text as UTC, so the
 *   rule sat hours away from the data point with the same timestamp;
 * - a rule's datum on a grid chart vanished (the index axis had no position
 *   for a value).
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { parseInstant, toOption } from "./option";
import { answer, base, f64, layer, q8, time } from "./testAnswer";

describe("instants as the sandbox reads them (UTC unless the text says otherwise)", () => {
  const tz = process.env.TZ;
  beforeEach(() => {
    process.env.TZ = "Asia/Taipei"; // UTC+8: a local reading is off by 8 h
  });
  afterEach(() => {
    process.env.TZ = tz;
  });

  // The sandbox's reading (wire.epoch_ms) is the oracle, written to a file.
  const corpus = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "wire-corpus", "instants.json");
  const cases = (JSON.parse(readFileSync(corpus, "utf8")) as { cases: { text: string; ms: number | null }[] }).cases;

  // A null is a form the schema's `$defs.instant` refuses: validate names it,
  // and the renderer places nothing rather than a date of its own reading.
  it.each(cases)("$text", ({ text, ms }) => {
    expect(parseInstant(text)).toBe(ms ?? Number.NaN);
  });

  it("puts a rule's date datum on the data point with the same timestamp", () => {
    const spec = {
      ...base,
      layer: [
        { mark: "line", encoding: { x: { field: "t", type: "temporal" }, y: { field: "v", type: "quantitative" } } },
        { mark: "rule", encoding: { x: { datum: "2024-03-01T12:00:00" } } },
      ],
    };
    const a = answer(layer("line", 1, { t: time(["2024-03-01T12:00:00Z"]), v: f64([1]) }), layer("rule", 0, {}));
    const series = toOption(spec, a).option.series as { data: number[][]; markLine?: { data: { xAxis: number }[] } }[];
    expect(series[1].markLine?.data[0].xAxis).toBe(series[0].data[0][0]);
  });
});

describe("a rule on a grid", () => {
  it("sits on the cell its datum names", () => {
    const spec = {
      ...base,
      layer: [
        {
          mark: "grid",
          encoding: {
            x: { field: "x", type: "ordinal" },
            y: { field: "y", type: "ordinal" },
            color: { field: "v", type: "quantitative" },
          },
        },
        { mark: "rule", encoding: { x: { datum: 12 } } },
      ],
    };
    const a = answer(
      layer("grid", 3, { x: f64([10, 11, 12]), y: f64([0, 0, 0]), v: q8([0, 1, 2], 0, 1) }),
      layer("rule", 0, {}),
    );
    const series = toOption(spec, a).option.series as { markLine?: { data: { xAxis: number }[] } }[];
    expect(series[1].markLine?.data[0].xAxis).toBe(2); // the third cell
  });
});
