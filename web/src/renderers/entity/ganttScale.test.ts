import { describe, expect, it } from "vitest";

import { applyDrag, daysBetween, deltaDays, pxPerDay, shiftDate, spanToDates } from "./ganttScale";

describe("shiftDate", () => {
  it("adds days in UTC without timezone drift, crossing month/year", () => {
    expect(shiftDate("2026-01-01", 5)).toBe("2026-01-06");
    expect(shiftDate("2026-01-31", 1)).toBe("2026-02-01");
    expect(shiftDate("2026-03-01", -1)).toBe("2026-02-28");
    expect(shiftDate("2026-12-31", 1)).toBe("2027-01-01");
  });
});

describe("pxPerDay", () => {
  it("is widest at day zoom, narrowest at month", () => {
    expect(pxPerDay("day")).toBeGreaterThan(pxPerDay("week"));
    expect(pxPerDay("week")).toBeGreaterThan(pxPerDay("month"));
  });
});

describe("deltaDays", () => {
  it("converts a pixel delta into whole days at the given zoom (nearest)", () => {
    const ppd = pxPerDay("day");
    expect(deltaDays(ppd * 3 + 1, "day")).toBe(3);
    expect(deltaDays(-ppd * 2, "day")).toBe(-2);
    expect(deltaDays(ppd * 0.4, "day")).toBe(0);
  });
});

describe("daysBetween", () => {
  it("counts whole UTC days from a to b", () => {
    expect(daysBetween("2026-01-01", "2026-01-11")).toBe(10);
    expect(daysBetween("2026-01-11", "2026-01-01")).toBe(-10);
  });
});

describe("spanToDates", () => {
  it("parses start/end string / list / object to YYYY-MM-DD", () => {
    expect(spanToDates("2026-01-10/2026-01-20")).toEqual({ start: "2026-01-10", end: "2026-01-20" });
    expect(spanToDates(["2026-01-10", "2026-01-20"])).toEqual({ start: "2026-01-10", end: "2026-01-20" });
    expect(spanToDates({ start: "2026-01-10", end: "2026-01-20" })).toEqual({ start: "2026-01-10", end: "2026-01-20" });
  });
  it("returns null for junk or a reversed range", () => {
    expect(spanToDates("nope")).toBeNull();
    expect(spanToDates("2026-02-01/2026-01-01")).toBeNull();
  });
});

describe("applyDrag", () => {
  const span = { start: "2026-01-10", end: "2026-01-20" };

  it("move shifts both ends, preserving duration", () => {
    expect(applyDrag(span, "move", 5)).toEqual({ start: "2026-01-15", end: "2026-01-25" });
    expect(applyDrag(span, "move", -4)).toEqual({ start: "2026-01-06", end: "2026-01-16" });
  });

  it("start resizes the left edge, clamped not to pass the end", () => {
    expect(applyDrag(span, "start", 3)).toEqual({ start: "2026-01-13", end: "2026-01-20" });
    expect(applyDrag(span, "start", 999)).toEqual({ start: "2026-01-20", end: "2026-01-20" });
  });

  it("end resizes the right edge, clamped not to precede the start", () => {
    expect(applyDrag(span, "end", -3)).toEqual({ start: "2026-01-10", end: "2026-01-17" });
    expect(applyDrag(span, "end", -999)).toEqual({ start: "2026-01-10", end: "2026-01-10" });
  });
});
