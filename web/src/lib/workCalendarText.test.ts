import { describe, expect, it } from "vitest";

import { formatOverrides, parseOverrides } from "./workCalendarText";

describe("work-calendar override text", () => {
  it("reads one date per line, both directions", () => {
    // `off` frees a working day, `work` claims a non-working one — the make-up
    // workday case is the whole reason this is a calendar.
    const { overrides, errors } = parseOverrides("2026-01-01=off\n2026-08-01=work");
    expect(errors).toEqual([]);
    expect(overrides).toEqual({ "2026-01-01": "off", "2026-08-01": "work" });
  });

  it("ignores blank lines and surrounding spaces", () => {
    const { overrides, errors } = parseOverrides("\n  2026-01-01 = off  \n\n");
    expect(errors).toEqual([]);
    expect(overrides).toEqual({ "2026-01-01": "off" });
  });

  it("names the line it could not read instead of dropping it", () => {
    // Silently skipping a typo'd line is how someone ends up believing a
    // holiday is recorded when it is not.
    const { overrides, errors } = parseOverrides("2026-01-01=off\nnew years day\n2026-13-99=off");
    expect(overrides).toEqual({ "2026-01-01": "off" });
    expect(errors).toHaveLength(2);
    expect(errors[0]).toContain("new years day");
    expect(errors[1]).toContain("2026-13-99");
  });

  it("rejects a value that is neither off nor work", () => {
    const { errors } = parseOverrides("2026-01-01=holiday");
    expect(errors[0]).toContain("holiday");
  });

  it("round-trips what it renders", () => {
    const text = formatOverrides({ "2026-08-01": "work", "2026-01-01": "off" });
    expect(text).toBe("2026-01-01=off\n2026-08-01=work"); // sorted by date
    expect(parseOverrides(text).overrides).toEqual({
      "2026-01-01": "off",
      "2026-08-01": "work",
    });
  });
});
