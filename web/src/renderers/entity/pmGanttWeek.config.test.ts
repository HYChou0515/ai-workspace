import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { weekLabelOf } from "./ganttScale";
import { parseViewSpec, setViewScalar } from "./shared";

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

  it("ships a 07:00–21:00 working day, read through the real parser (#785)", () => {
    // The window is dropped WHOLE on anything it cannot use, and it is dropped
    // silently — the same failure mode this file exists to catch for `week:`.
    // A quoting slip here would leave the chart drawing all twenty-four hours
    // with nothing on screen to say so.
    expect(parseViewSpec(yaml)?.work_hours).toEqual({ from: 7, to: 21 });
  });

  it("survives a gear edit to the working day, block form and all (#785)", () => {
    // The panel edits this file's TEXT, and the window ships as an indented
    // block sitting between the weekend flag and the time-axis comment. An edit
    // that replaced only the `work_hours:` line would orphan its two children
    // and make the whole file unparseable — which shows up not as a wrong
    // setting but as a blank view.
    const edited = setViewScalar(yaml, "work_hours", '{ from: "08:00", to: "18:00" }');
    const spec = parseViewSpec(edited);
    expect(spec?.work_hours).toEqual({ from: 8, to: 18 });
    expect(spec?.skip_weekends).toBe(true);
    expect(spec?.week?.label).toBe("W{y1}{ww}");
    // Something declared AFTER the edited block, so the assertion actually
    // proves the edit did not swallow the rest of the file. This used to be
    // `schedule.duration`; the time-axis keys are what sits there now.
    expect(spec?.weekday).toBe("number");
    expect(spec?.day_of_month).toBe("hidden");
    expect(edited).toContain("# ── Working day");
  });

  it("yields the exact codes the file's own comment promises", () => {
    const rule = parseViewSpec(yaml)!.week!;
    expect(weekLabelOf("2026-01-01", rule, "2026-06-01")).toBe("W601"); // 2026 W01
    // the 2026/2027 cross-year week flips with today:
    expect(weekLabelOf("2026-12-31", rule, "2026-06-01")).toBe("W653"); // before New Year
    expect(weekLabelOf("2026-12-31", rule, "2027-06-01")).toBe("W701"); // on/after New Year
  });
});
