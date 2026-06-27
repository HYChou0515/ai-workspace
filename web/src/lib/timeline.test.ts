import { describe, expect, it } from "vitest";

import type { StepStateDTO } from "../api/workflows";
import { GAP_MS, followOffset, timelineModel } from "./timeline";

const step = (over: Partial<StepStateDTO>): StepStateDTO => ({
  phase: "p",
  name: "n",
  key: "",
  status: "passed",
  attempts: 0,
  reason: "",
  started: null,
  ended: null,
  ...over,
});

describe("timelineModel", () => {
  it("is empty when no step recorded a start", () => {
    expect(timelineModel([], 100)).toEqual({ bars: [], gaps: [], totalMs: 0 });
    // a cache-skip never started → no bar
    expect(timelineModel([step({ started: null, ended: null })], 100).bars).toEqual([]);
  });

  it("lays sequential steps back-to-back and excludes the idle wait between them", () => {
    // step A [0,10], a 1000ms idle wait, step B [1010,1020]
    const m = timelineModel(
      [
        step({ name: "a", started: 0, ended: 10 }),
        step({ name: "b", started: 1010, ended: 1020 }),
      ],
      2000,
    );
    expect(m.bars[0]).toMatchObject({ x0: 0, x1: 10 });
    // B starts right after A's 10ms + one fixed gap marker — the 1000ms wait is gone
    expect(m.bars[1]).toMatchObject({ x0: 10 + GAP_MS, x1: 20 + GAP_MS });
    expect(m.gaps).toEqual([{ x: 10, realMs: 1000 }]);
    expect(m.totalMs).toBe(20 + GAP_MS);
  });

  it("uses `now` as the end of a still-running step", () => {
    const m = timelineModel([step({ name: "live", started: 100, ended: null })], 160);
    expect(m.bars[0]).toMatchObject({ x0: 0, x1: 60 });
  });

  it("merges overlapping (parallel) steps into one covered stretch — both bars show", () => {
    const m = timelineModel(
      [
        step({ name: "a", started: 0, ended: 30 }),
        step({ name: "b", started: 10, ended: 20 }), // fully inside A's span
      ],
      100,
    );
    expect(m.bars).toHaveLength(2);
    // no idle gap (they overlap), so total is just the covered span
    expect(m.gaps).toEqual([]);
    expect(m.totalMs).toBe(30);
  });
});

describe("followOffset", () => {
  it("pins the right edge (now) when following", () => {
    expect(followOffset(1000, 300, true, 0)).toBe(700);
  });

  it("respects a manual pan (clamped) when not following", () => {
    expect(followOffset(1000, 300, false, 200)).toBe(200);
    expect(followOffset(1000, 300, false, 9999)).toBe(700); // clamped to max
    expect(followOffset(1000, 300, false, -50)).toBe(0); // clamped to 0
  });
});
