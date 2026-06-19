import { describe, expect, it } from "vitest";

import { mapWithConcurrency } from "./concurrency";

describe("mapWithConcurrency", () => {
  it("maps every item, preserving input order", async () => {
    const out = await mapWithConcurrency([1, 2, 3, 4], 2, async (n) => n * 10);
    expect(out).toEqual([10, 20, 30, 40]);
  });

  it("never runs more than `limit` tasks at once", async () => {
    let active = 0;
    let peak = 0;
    const items = Array.from({ length: 12 }, (_, i) => i);
    await mapWithConcurrency(items, 3, async (i) => {
      active++;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 1));
      active--;
      return i;
    });
    // The whole point of the fix: a folder's worth of files must NOT fan out
    // all at once (that froze the tab); at most `limit` are in flight.
    expect(peak).toBe(3);
  });

  it("returns [] for no items (and runs nothing)", async () => {
    let ran = 0;
    const out = await mapWithConcurrency([], 4, async (x) => {
      ran++;
      return x;
    });
    expect(out).toEqual([]);
    expect(ran).toBe(0);
  });
});
