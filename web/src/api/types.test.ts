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
  // Built from LOCAL parts, not ISO strings: the rule is about the calendar day
  // the reader is on, so a fixture pinned to UTC would mean something different
  // in every timezone the suite runs in.
  const at = (y: number, m: number, d: number, h: number, min = 0, sec = 0) =>
    new Date(y, m, d, h, min, sec);
  const NOW = at(2026, 4, 23, 12, 0); // 23 May 2026, midday

  it("returns 'just now' for <60s ago", () => {
    expect(relativeTime(at(2026, 4, 23, 11, 59, 30).toISOString(), NOW)).toBe("just now");
  });

  it("returns minutes for sub-hour", () => {
    expect(relativeTime(at(2026, 4, 23, 11, 48).toISOString(), NOW)).toBe("12 min ago");
  });

  it("returns hours for sub-day", () => {
    expect(relativeTime(at(2026, 4, 23, 7, 0).toISOString(), NOW)).toBe("5 h ago");
  });

  it("returns days once a whole day has elapsed", () => {
    expect(relativeTime(at(2026, 4, 20, 12, 0).toISOString(), NOW)).toBe("3 d ago");
  });

  it("returns '—' for invalid timestamps", () => {
    expect(relativeTime("nonsense", NOW)).toBe("—");
  });

  // The crossover is the point of this function: an interval answers "is this
  // current?" while the answer is still small. Past a week it stops answering
  // anything — nobody converts "412 d ago" into a date in their head.
  it("switches to a date once it is more than a week old", () => {
    expect(relativeTime(at(2026, 4, 16, 12, 0).toISOString(), NOW)).toBe("7 d ago");
    expect(relativeTime(at(2026, 4, 15, 12, 0).toISOString(), NOW)).toBe("15 May");
  });

  it("formats the date in the reader's own timezone", () => {
    // Early enough in the local morning that the UTC date is the PREVIOUS day
    // east of Greenwich (and late enough in the evening that it is the NEXT one
    // west of it) — formatting in UTC names the wrong day for both. Asserted
    // against the local parts rather than a literal, because the ONLY machine
    // where the two agree is one running in UTC, and there is nothing to catch
    // there.
    for (const when of [at(2026, 3, 3, 0, 30), at(2026, 3, 3, 23, 30)]) {
      const expected = `${when.getDate()} ${
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][
          when.getMonth()
        ]
      }`;
      expect(relativeTime(when.toISOString(), NOW)).toBe(expected);
    }
  });

  it("keeps the year off a date in the current year, and on for an older one", () => {
    // The year is noise for the common case and essential for the rare one.
    expect(relativeTime(at(2026, 0, 4, 9, 0).toISOString(), NOW)).toBe("4 Jan");
    expect(relativeTime(at(2025, 10, 30, 9, 0).toISOString(), NOW)).toBe("30 Nov 2025");
  });

  it("never shows a future timestamp as a date", () => {
    // Clock skew between the server that stamped it and the browser reading it
    // is normal; "just now" is the honest reading, not tomorrow's date.
    expect(relativeTime(at(2026, 4, 23, 12, 5).toISOString(), NOW)).toBe("just now");
    expect(relativeTime(at(2026, 4, 24, 9, 0).toISOString(), NOW)).toBe("just now");
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
