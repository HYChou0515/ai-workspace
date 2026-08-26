import { describe, expect, it } from "vitest";

import {
  formatInvestigationId,
  isCritical,
  isOpen,
  relativeTime,
  summarize,
} from "./types";

describe("formatInvestigationId", () => {
  it("takes the first 8 hex of a specstar uuid, no prefix", () => {
    expect(
      formatInvestigationId("investigation:7855cbe8-2c14-4cff-a55d-64a1d6f71c56"),
    ).toBe("7855cbe8");
  });

  it("strips dashes before truncating", () => {
    expect(formatInvestigationId("investigation:ab-cd-ef-12-34")).toBe("abcdef12");
  });

  it("handles a bare id without a colon", () => {
    expect(formatInvestigationId("local-1")).toBe("local1");
  });
});

describe("summarize", () => {
  it("returns the first non-empty line of a multi-line description", () => {
    expect(summarize("\n\nFirst line.\nSecond line.")).toBe("First line.");
  });

  it("returns the empty string for an all-blank description", () => {
    expect(summarize("\n  \n\t\n")).toBe("");
  });
});

describe("relativeTime", () => {
  const NOW = new Date("2026-05-23T12:00:00Z");

  it("returns 'just now' for <60s ago", () => {
    expect(relativeTime("2026-05-23T11:59:30Z", NOW)).toBe("just now");
  });

  it("returns minutes for sub-hour", () => {
    expect(relativeTime("2026-05-23T11:48:00Z", NOW)).toBe("12 min ago");
  });

  it("returns hours for sub-day", () => {
    expect(relativeTime("2026-05-23T07:00:00Z", NOW)).toBe("5 h ago");
  });

  it("returns days for >=24h", () => {
    expect(relativeTime("2026-05-20T12:00:00Z", NOW)).toBe("3 d ago");
  });

  it("returns '—' for invalid timestamps", () => {
    expect(relativeTime("nonsense", NOW)).toBe("—");
  });

  // Past a week "N d ago" stops being an answer: nobody converts "412 d ago"
  // into a date in their head, and the further back it goes the more precision
  // it claims and the less it tells you. A date is the useful form there.
  it("switches to a date once it is more than a week old", () => {
    expect(relativeTime("2026-05-16T12:00:00Z", NOW)).toBe("7 d ago");
    expect(relativeTime("2026-05-15T12:00:00Z", NOW)).toBe("15 May");
  });

  it("keeps the year off a date in the current year, and on for an older one", () => {
    // The year is noise for the common case and essential for the rare one.
    expect(relativeTime("2026-01-04T12:00:00Z", NOW)).toBe("4 Jan");
    expect(relativeTime("2025-11-30T12:00:00Z", NOW)).toBe("30 Nov 2025");
  });

  it("never shows a future timestamp as a date", () => {
    // Clock skew between the server that stamped it and the browser reading it
    // is normal; "just now" is the honest reading, not a date in the future.
    expect(relativeTime("2026-05-23T12:05:00Z", NOW)).toBe("just now");
  });
});

describe("isCritical / isOpen", () => {
  it("isCritical is true only for P0 and P1", () => {
    expect(isCritical("P0")).toBe(true);
    expect(isCritical("P1")).toBe(true);
    expect(isCritical("P2")).toBe(false);
    expect(isCritical("P3")).toBe(false);
    expect(isCritical("P4")).toBe(false);
  });

  it("isOpen is true for triaging and awaiting_review", () => {
    expect(isOpen("triaging")).toBe(true);
    expect(isOpen("awaiting_review")).toBe(true);
    expect(isOpen("resolved")).toBe(false);
    expect(isOpen("abandoned")).toBe(false);
  });
});
