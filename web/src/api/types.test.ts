import { describe, expect, it } from "vitest";

import {
  formatInvestigationId,
  isCritical,
  isOpen,
  relativeTime,
  summarize,
} from "./types";

describe("formatInvestigationId", () => {
  it("passes already-formatted ids through unchanged", () => {
    expect(formatInvestigationId("INC-2026-0142")).toBe("INC-2026-0142");
  });

  it("upgrades a specstar-style raw id (`inv-2026-0142`) to display form", () => {
    expect(formatInvestigationId("inv-2026-0142")).toBe("INC-2026-0142");
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
