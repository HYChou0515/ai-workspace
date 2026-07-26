import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { weekLabelOf } from "./ganttScale";
import { parseViewSpec } from "./shared";

// Pins the SHIPPED PM gantt view file: a YAML typo or a wrong value would
// silently drop the `week:` rule (parseViewSpec never throws — it just omits
// unknown/broken keys), turning the timeline back to plain day labels with no
// error. This test reads the real file so that can't regress unnoticed.
const yaml = readFileSync(
  resolve(process.cwd(), "../src/workspace_app/apps/pm/profiles/default/views/gantt.ai.yaml"),
  "utf8",
);

describe("shipped PM gantt view — custom week rule", () => {
  it("parses to the documented non-ISO work-week rule", () => {
    const spec = parseViewSpec(yaml);
    expect(spec?.week).toEqual({
      start: "monday",
      first_week: "jan1",
      reset: "yearly",
      boundary: "by_today",
      label: "W{y1}{ww}",
    });
  });

  it("ships with the working-day (skip weekends) option on", () => {
    expect(parseViewSpec(yaml)?.skip_weekends).toBe(true);
  });

  it("yields the exact codes the file's own comment promises", () => {
    const rule = parseViewSpec(yaml)!.week!;
    expect(weekLabelOf("2026-01-01", rule, "2026-06-01")).toBe("W601"); // 2026 W01
    // the 2026/2027 cross-year week flips with today:
    expect(weekLabelOf("2026-12-31", rule, "2026-06-01")).toBe("W653"); // before New Year
    expect(weekLabelOf("2026-12-31", rule, "2027-06-01")).toBe("W701"); // on/after New Year
  });
});
