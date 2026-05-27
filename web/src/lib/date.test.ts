import { describe, expect, it } from "vitest";

import { ymd } from "./date";

describe("ymd", () => {
  it("formats an ISO timestamp as YYYY/MM/DD (zero-padded, not US M/D/Y)", () => {
    const out = ymd("2026-05-27T12:00:00Z");
    expect(out).toMatch(/^\d{4}\/\d{2}\/\d{2}$/);
    expect(out.startsWith("2026/")).toBe(true);
  });

  it("zero-pads single-digit months and days", () => {
    expect(ymd("2026-03-04T12:00:00Z")).toMatch(/^2026\/03\/04$/);
  });
});
