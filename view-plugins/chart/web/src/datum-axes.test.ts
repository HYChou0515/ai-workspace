/**
 * Which axis a rule's datum lands on, held to the sandbox's verdict
 * (`wire-corpus/datum-axes.json`, written from `validate.check`): the
 * renderer gives a datum a numeric position exactly when validate accepts it,
 * and leaves out one it refuses (with a note, see echarts.real.test.ts).
 * The axis choice (the first layer's channel with a field, a grid's cells, a
 * category's labels) is decided once on each side; this file is the oracle
 * that keeps them one decision (review round 4).
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { toOption, type WireLayer } from "./option";
import { answer, base, cat, f64, layer, time } from "./testAnswer";
import type { WireColumn } from "./wire";

type Channel = { field?: string; type?: string; datum?: string | number };
type Layer = { mark: string; encoding: Record<string, Channel> };
type Doc = {
  data: Record<string, (string | number)[]>;
  cases: { name: string; layer: Layer[]; placed: boolean }[];
};

const file = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "wire-corpus", "datum-axes.json");
const doc = JSON.parse(readFileSync(file, "utf8")) as Doc;

/** A layer's rows as the sandbox would send them for this corpus's frame. */
function wireLayer(l: Layer): WireLayer {
  const columns: Record<string, WireColumn> = {};
  for (const c of Object.values(l.encoding)) {
    if (!c.field) continue;
    const values = doc.data[c.field];
    columns[c.field] =
      c.type === "temporal" ? time(values as string[]) : c.type === "quantitative" ? f64(values as number[]) : cat(values);
  }
  const rows = Object.keys(columns).length ? doc.data.t.length : 0;
  return layer(l.mark, rows, columns);
}

type Series = { markLine?: { data: Record<string, unknown>[] } };

describe("a rule's datum is drawn exactly where validate lets it through", () => {
  const tz = process.env.TZ;
  beforeEach(() => {
    process.env.TZ = "Asia/Taipei";
  });
  afterEach(() => {
    process.env.TZ = tz;
  });

  it.each(doc.cases)("$name", ({ layer: layers, placed }) => {
    const spec = { ...base, layer: layers };
    const series = toOption(spec, answer(...layers.map(wireLayer))).option.series as Series[];
    const line = series.find((s) => s.markLine)?.markLine?.data[0] ?? {};
    const at = line.xAxis ?? line.yAxis;
    expect(typeof at === "number" && Number.isFinite(at)).toBe(placed);
  });
});
