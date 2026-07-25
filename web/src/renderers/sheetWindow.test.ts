import { describe, expect, it } from "vitest";

import { visibleRange } from "./sheetWindow";

describe("visibleRange", () => {
  it("covers the scrolled-to window plus overscan", () => {
    // 400px tall viewport, 20px rows => 20 rows on screen, starting at row 25.
    expect(visibleRange({ scrollTop: 500, viewportHeight: 400, rowHeight: 20, total: 1000, overscan: 5 })).toEqual({
      start: 20,
      end: 50,
    });
  });

  it("clamps to the ends of the data", () => {
    expect(visibleRange({ scrollTop: 0, viewportHeight: 400, rowHeight: 20, total: 8, overscan: 5 })).toEqual({
      start: 0,
      end: 8,
    });
  });

  it("renders a first screenful when the viewport has not been measured yet", () => {
    // Before layout (and in happy-dom) clientHeight is 0. Deriving the window
    // from that literally would render NOTHING, so the grid would look empty on
    // first paint and in every component test.
    const range = visibleRange({ scrollTop: 0, viewportHeight: 0, rowHeight: 20, total: 1000, overscan: 5 });
    expect(range.start).toBe(0);
    expect(range.end).toBeGreaterThan(0);
  });
});
