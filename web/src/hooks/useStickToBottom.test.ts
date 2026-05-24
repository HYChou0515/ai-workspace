import { describe, expect, it } from "vitest";

import { isNearBottom } from "./useStickToBottom";

describe("isNearBottom", () => {
  it("is true at the very bottom", () => {
    // scrollTop 800, client 200, height 1000 → 1000-800-200 = 0
    expect(isNearBottom(800, 200, 1000)).toBe(true);
  });
  it("is true within the threshold", () => {
    expect(isNearBottom(780, 200, 1000, 24)).toBe(true); // 20px from bottom
  });
  it("is false when scrolled up past the threshold", () => {
    expect(isNearBottom(500, 200, 1000, 24)).toBe(false); // 300px from bottom
  });
});
