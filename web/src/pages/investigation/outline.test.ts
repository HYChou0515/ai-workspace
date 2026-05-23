import { describe, expect, it } from "vitest";

import { extractHeadings } from "./InvestigationShell";
import { hasOutline } from "./renderer";

describe("hasOutline", () => {
  it("is true for markdown files", () => {
    expect(hasOutline("/brief.md")).toBe(true);
    expect(hasOutline("/5-why.md")).toBe(true);
  });

  it("is true for report version files", () => {
    expect(hasOutline("/report.v1.md")).toBe(true);
    expect(hasOutline("/report.v12.md")).toBe(true);
  });

  it("is false for notebooks / canvas / csv", () => {
    expect(hasOutline("/drift.ipynb")).toBe(false);
    expect(hasOutline("/fishbone.canvas")).toBe(false);
    expect(hasOutline("/data/x.csv")).toBe(false);
  });
});

describe("extractHeadings", () => {
  it("pulls atx headings with their levels", () => {
    const md = "# Title\n\nbody\n\n## Section A\n\ntext\n\n### Sub\n";
    expect(extractHeadings(md)).toEqual([
      { level: 1, text: "Title" },
      { level: 2, text: "Section A" },
      { level: 3, text: "Sub" },
    ]);
  });

  it("handles the seeded brief.md shape", () => {
    const md = [
      "# Solder voids spike",
      "",
      "- **Owner**: alice",
      "",
      "## Initial observation",
      "",
      "## Hypotheses",
    ].join("\n");
    expect(extractHeadings(md).map((h) => h.text)).toEqual([
      "Solder voids spike",
      "Initial observation",
      "Hypotheses",
    ]);
  });

  it("returns empty for heading-less content", () => {
    expect(extractHeadings("just prose\nno headings")).toEqual([]);
  });
});
