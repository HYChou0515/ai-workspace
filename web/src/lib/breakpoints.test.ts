import { describe, expect, it } from "vitest";

import { BREAKPOINTS, NARROW_QUERY } from "./breakpoints";

describe("breakpoints (#464)", () => {
  it("keeps the narrow media query one px below the narrow breakpoint", () => {
    // The CSS @media blocks use `max-width: 767px`; the JS query must match, or
    // the JS-driven shells and the CSS-driven KB grids would flip at different
    // widths and disagree by a pixel.
    expect(NARROW_QUERY).toBe(`(max-width: ${BREAKPOINTS.narrow - 1}px)`);
    expect(NARROW_QUERY).toContain("767");
  });

  it("orders narrow below wide", () => {
    expect(BREAKPOINTS.narrow).toBeLessThan(BREAKPOINTS.wide);
  });
});
