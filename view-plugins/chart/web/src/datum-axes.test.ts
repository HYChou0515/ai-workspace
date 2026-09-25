/**
 * Which axis a rule's datum lands on, held to the sandbox's verdict
 * (`wire-corpus/datum-axes.json`, written from `validate.check`, with each
 * case's real query answer): fed that answer, the renderer gives a datum a
 * numeric position exactly when validate accepts it, and leaves out one it
 * refuses (with a note, see echarts.real.test.ts). The axis choice — a grid's
 * channels when any layer is a grid, otherwise the first layer's channel with
 * a field — and the values each axis holds are decided once on each side;
 * this file is the oracle that keeps them one decision (review rounds 4-6).
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { type Answer, toOption } from "./option";

type Doc = { cases: { name: string; spec: object; answer: Answer; placed: boolean }[] };

const file = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "wire-corpus", "datum-axes.json");
const doc = JSON.parse(readFileSync(file, "utf8")) as Doc;

type Series = { markLine?: { data: Record<string, unknown>[] } };

describe("a rule's datum is drawn exactly where validate lets it through", () => {
  const tz = process.env.TZ;
  beforeEach(() => {
    process.env.TZ = "Asia/Taipei";
  });
  afterEach(() => {
    process.env.TZ = tz;
  });

  it.each(doc.cases)("$name", ({ spec, answer, placed }) => {
    const series = toOption(spec, answer).option.series as Series[];
    const line = series.find((s) => s.markLine)?.markLine?.data[0] ?? {};
    const at = line.xAxis ?? line.yAxis;
    expect(typeof at === "number" && Number.isFinite(at)).toBe(placed);
  });
});
