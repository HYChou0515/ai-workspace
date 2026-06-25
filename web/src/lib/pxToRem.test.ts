import { describe, expect, it } from "vitest";

import { pxToRem } from "./pxToRem";

describe("pxToRem", () => {
  it("expresses a pixel font size as rem relative to a 16px root", () => {
    expect(pxToRem(16)).toBe("1rem");
    expect(pxToRem(14)).toBe("0.875rem");
    expect(pxToRem(13)).toBe("0.8125rem");
  });

  it("maps zero to a unit-less zero", () => {
    expect(pxToRem(0)).toBe("0");
  });
});
