import { describe, expect, it } from "vitest";

import { qualityTone } from "./quality";

describe("qualityTone", () => {
  it("maps a score to a coarse tone band", () => {
    expect(qualityTone(85)).toBe("good");
    expect(qualityTone(70)).toBe("good");
    expect(qualityTone(55)).toBe("ok");
    expect(qualityTone(40)).toBe("ok");
    expect(qualityTone(20)).toBe("bad");
    expect(qualityTone(0)).toBe("bad");
  });

  it("treats un-scored (null/undefined) as no tone — neutral, no badge", () => {
    expect(qualityTone(null)).toBeNull();
    expect(qualityTone(undefined)).toBeNull();
  });
});
