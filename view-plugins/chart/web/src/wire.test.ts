/**
 * The wire format, decoded here and encoded by the sandbox (`chart_view/wire.py`).
 * Both halves are held to `wire-corpus/`: the sandbox's test to values → wire,
 * this one to wire → values.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { canon, decodeBits, decodeColumn, type WireColumn } from "./wire";

const corpus = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "wire-corpus");
type Case = { kind: string; values: unknown[]; wire: WireColumn | string };
const cases = readdirSync(corpus)
  .filter((n) => n.endsWith(".json") && n !== "canon.json")
  .sort()
  .map((n) => [n, JSON.parse(readFileSync(join(corpus, n), "utf8")) as Case] as const);

describe("wire corpus", () => {
  it("covers every kind", () => {
    expect(new Set(cases.map(([, c]) => c.kind))).toEqual(new Set(["f64", "time", "cat", "q8", "bits"]));
  });

  it.each(cases)("%s decodes to its values", (_name, c) => {
    if (c.kind === "bits") {
      expect(Array.from(decodeBits(c.wire as string, c.values.length))).toEqual(c.values);
      return;
    }
    const col = decodeColumn(c.wire as WireColumn);
    expect(col.length).toBe(c.values.length);
    c.values.forEach((v, i) => {
      const got = col.value(i);
      if (v === null) expect(got).toBeNull();
      else if (c.kind === "time") expect(got).toBe(Date.parse(v as string));
      else if (c.kind === "q8") {
        const w = c.wire as Extract<WireColumn, { kind: "q8" }>;
        // Quantized to 255 levels: within half a level of the value.
        expect(Math.abs((got as number) - (v as number))).toBeLessThanOrEqual((w.max - w.min) / 254 / 2 + 1e-12);
      } else expect(got).toBe(v);
    });
  });
});

describe("canon", () => {
  const table = JSON.parse(readFileSync(join(corpus, "canon.json"), "utf8")).cases as { value: unknown; text: string }[];

  it.each(table)("$value → $text", ({ value, text }) => {
    expect(canon(value)).toBe(text);
  });

  it.each([null, undefined, Number.NaN, Number.POSITIVE_INFINITY])("%s has no marking string", (v) => {
    expect(canon(v)).toBeNull();
  });
});
