import { describe, expect, it } from "vitest";

import { durationParts } from "./duration";

describe("durationParts", () => {
  it("reads seconds under a minute, minutes under an hour, hours above", () => {
    expect(durationParts(0)).toEqual({ unit: "seconds", n: 0 });
    expect(durationParts(42_000)).toEqual({ unit: "seconds", n: 42 });
    expect(durationParts(5 * 60_000)).toEqual({ unit: "minutes", n: 5 });
    expect(durationParts(90 * 60_000)).toEqual({ unit: "hours", n: "1.5" });
  });

  it("picks the unit after rounding, so a number never reaches the next unit", () => {
    // 59.6 s rounds to 60: one minute, not "60 s".
    expect(durationParts(59_600)).toEqual({ unit: "minutes", n: 1 });
    // 59 min 40 s rounds to 60 min: an hour, not "60 min".
    expect(durationParts(3_580_000)).toEqual({ unit: "hours", n: "1.0" });
  });
});
