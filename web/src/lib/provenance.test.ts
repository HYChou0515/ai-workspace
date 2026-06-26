import { describe, expect, it } from "vitest";

import type { MsgKey } from "./i18n";
import { formatProvenance } from "./provenance";

// Stub translator: English labels, unknown keys pass through.
const t = ((k: MsgKey) =>
  (({
    "cite.loc.page": "Page",
    "cite.loc.slide": "Slide",
    "cite.loc.sheet": "Sheet",
    "cite.loc.line": "Line",
    "cite.loc.row": "Row",
  }) as Record<string, string>)[k] ?? k) as (k: MsgKey) => string;

describe("formatProvenance (#254)", () => {
  it("renders contiguous pages as a range with the section breadcrumb", () => {
    expect(
      formatProvenance({ page: [3, 4], section: ["Failure Analysis > Root Cause"] }, t),
    ).toBe("Page 3–4 · Failure Analysis > Root Cause");
  });

  it("renders a single page", () => {
    expect(formatProvenance({ page: [3], section: ["Intro"] }, t)).toBe("Page 3 · Intro");
  });

  it("lists non-contiguous pages instead of a range", () => {
    expect(formatProvenance({ page: [3, 7] }, t)).toBe("Page 3, 7");
  });

  it("labels a sheet by name", () => {
    expect(formatProvenance({ sheet: ["Q3"] }, t)).toBe("Sheet Q3");
  });

  it("is empty for no provenance", () => {
    expect(formatProvenance({}, t)).toBe("");
    expect(formatProvenance(undefined, t)).toBe("");
  });
});
