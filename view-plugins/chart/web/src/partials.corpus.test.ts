/**
 * #861 D3 parity: the browser's fold of a bar's partials is the sandbox's
 * own aggregate over the same picked rows. The oracle is
 * `wire-corpus/bar-partials.json`, written by the sandbox's
 * `scripts/write_partials_corpus.py` (`aggregate` over the picked rows alone)
 * and held current by its `test_partials.py`; nothing here restates it.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { markingFrom } from "../../../../web/src/lib/markings";
import { foldPartials, forKeys, litValues, type PartialsAnswer } from "./partials";

type Case = { name: string; by: string[]; picks: string[][]; partials: PartialsAnswer; oracle: ((number | null)[] | null)[] };

const file = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "wire-corpus", "bar-partials.json");
const cases = (JSON.parse(readFileSync(file, "utf8")) as { cases: Case[] }).cases;

describe("a bar's picked part, folded in the browser (#861 D3)", () => {
  it("covers every op the sandbox splits a bar for", () => {
    const ops = new Set(cases.flatMap((c) => c.partials.layers.flatMap((l) => (l && "op" in l ? [l.op] : []))));
    expect([...ops].sort()).toEqual(["count", "max", "mean", "min", "rate", "sum"]);
  });

  it.each(cases.map((c) => [c.name, c] as const))("%s: is the sandbox's aggregate over the picked rows", (_, c) => {
    const marking = markingFrom(c.by, c.picks);
    c.partials.layers.forEach((layer, li) => {
      const got = litValues(layer, marking);
      const want = c.oracle[li];
      if (want === null) {
        expect(got).toBeNull();
        return;
      }
      expect(got).not.toBeNull();
      expect(got!.length).toBe(want!.length);
      got!.forEach((v, b) => {
        const w = want![b];
        if (w === null || v === null) expect(v).toBe(w);
        else expect(v).toBeCloseTo(w, 12);
      });
    });
  });
});

describe("partials made for other keys", () => {
  const c = cases[0]!;
  const layer = c.partials.layers[0]!;

  it("are not used: the bar is lit whole until the answer for these keys comes", () => {
    const other = markingFrom(["group"], [["g1"]]);
    expect(forKeys(layer, other)).toBe(false);
    expect(litValues(layer, other)).toBeNull();
    expect(litValues(null, other)).toBeNull();
    expect(litValues({ whole: "why" }, other)).toBeNull();
  });
});

describe("foldPartials", () => {
  it("is null for a bar with nothing picked", () => {
    expect(foldPartials("count", [])).toBeNull();
  });

  it("a mean of no numbers has no value; a count of them is 0", () => {
    expect(foldPartials("mean", [[0, 0, 0, null, null]])).toBeNull();
    expect(foldPartials("count", [[0, 0, 0, null, null]])).toBe(0);
    expect(foldPartials("min", [[0, 0, 0, null, null]])).toBeNull();
  });
});
