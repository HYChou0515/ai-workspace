import { describe, expect, it } from "vitest";

import {
  applyDrag,
  AXIS_MIN_LABEL_PX,
  axisFor,
  canvasWidthFor,
  clampPpd,
  daysBetween,
  deltaDays,
  PPD_ANCHORS,
  ppdToSlider,
  pxPerDay,
  shiftDate,
  sliderToPpd,
  spanToDates,
  visibleDaysFor,
  formatWeekLabel,
  weekLabelOf,
  weekNumberOf,
  type WeekRule,
  weekStart,
} from "./ganttScale";

// The user's real convention (Taiwanese manufacturing work-week, deliberately
// NOT ISO): weeks run Mon→Sun, week 1 is the week containing Jan 1, numbering
// resets each year, and the cross-year week's label follows "today".
const WW: WeekRule = { start: "monday", first_week: "jan1", reset: "yearly", boundary: "by_today", label: "W{y1}{ww}" };

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
  it("converts a pixel delta into whole days at the given px-per-day (nearest)", () => {
    const ppd = pxPerDay("day");
    expect(deltaDays(ppd * 3 + 1, ppd)).toBe(3);
    expect(deltaDays(-ppd * 2, ppd)).toBe(-2);
    expect(deltaDays(ppd * 0.4, ppd)).toBe(0);
  });
  it("accepts an arbitrary continuous px-per-day (a slider density, not just an anchor)", () => {
    expect(deltaDays(45, 15)).toBe(3);
    expect(deltaDays(20, 15)).toBe(1);
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

describe("axisFor", () => {
  it("never spaces two fine labels closer than the min label width (fixes day-zoom overlap)", () => {
    // day anchor over a full month — the exact case that used to overlap MM-DD every 28px
    const axis = axisFor("2026-07-01", 31, PPD_ANCHORS.day);
    expect(axis.fine.length).toBeGreaterThan(1); // still shows ticks, doesn't go blank
    for (let i = 1; i < axis.fine.length; i++) {
      const gapPx = (axis.fine[i].day - axis.fine[i - 1].day) * PPD_ANCHORS.day;
      expect(gapPx).toBeGreaterThanOrEqual(AXIS_MIN_LABEL_PX);
    }
  });

  it("keeps labels non-overlapping across the whole zoom range", () => {
    for (const ppd of [3, 4.9, 5, 10, 18, 28]) {
      const axis = axisFor("2026-03-15", 500, ppd);
      for (let i = 1; i < axis.fine.length; i++) {
        const gapPx = (axis.fine[i].day - axis.fine[i - 1].day) * ppd;
        expect(gapPx).toBeGreaterThanOrEqual(AXIS_MIN_LABEL_PX);
      }
    }
  });

  it("zoomed in: day-number fine ticks under month bands (best form 天=月帶+日號)", () => {
    const axis = axisFor("2026-07-01", 40, PPD_ANCHORS.day);
    expect(axis.coarse.map((b) => b.label)).toContain("Jul 2026");
    expect(axis.coarse.map((b) => b.label)).toContain("Aug 2026");
    expect(axis.fine.every((t) => /^\d{1,2}$/.test(t.label))).toBe(true);
  });

  it("zoomed out: month-name fine ticks under year bands (best form 月=年帶+月份)", () => {
    const axis = axisFor("2026-01-01", 400, PPD_ANCHORS.month);
    expect(axis.unit).toBe("month");
    expect(axis.coarse.map((b) => b.label)).toEqual(expect.arrayContaining(["2026", "2027"]));
    expect(axis.fine.some((t) => t.label === "Feb")).toBe(true);
  });

  it("with a week rule, the detail fine row shows week codes at week starts (not day numbers), over month bands", () => {
    // 2026-06-29 is a Monday → day 0 is a week start; at the day anchor every
    // week (7·28px) clears the label width, so ticks are one week apart.
    const axis = axisFor("2026-06-29", 60, PPD_ANCHORS.day, WW, "2026-08-01");
    expect(axis.unit).toBe("week");
    expect(axis.fine.every((t) => /^W\d{3}$/.test(t.label))).toBe(true);
    expect(axis.fine[0]).toEqual({ day: 0, label: "W627" });
    expect(axis.fine[1].day).toBe(7);
    expect(axis.coarse.map((b) => b.label)).toContain("Jul 2026");
  });

  it("week-code fine labels never overlap across the zoom range (thinned to whole weeks)", () => {
    for (const ppd of [5, 10, 18, 28]) {
      const axis = axisFor("2026-03-15", 400, ppd, WW, "2026-08-01");
      for (let i = 1; i < axis.fine.length; i++) {
        const gapPx = (axis.fine[i].day - axis.fine[i - 1].day) * ppd;
        expect(gapPx).toBeGreaterThanOrEqual(AXIS_MIN_LABEL_PX);
        expect((axis.fine[i].day - axis.fine[i - 1].day) % 7).toBe(0); // whole-week steps
      }
    }
  });

  it("without a week rule the axis is unchanged — day numbers, not week codes", () => {
    const axis = axisFor("2026-06-29", 60, PPD_ANCHORS.day);
    expect(axis.fine.every((t) => /^\d{1,2}$/.test(t.label))).toBe(true);
  });

  it("coarse bands tile the whole visible window with no gaps", () => {
    const axis = axisFor("2026-07-10", 120, PPD_ANCHORS.week);
    expect(axis.coarse[0].day).toBe(0);
    for (let i = 1; i < axis.coarse.length; i++) {
      expect(axis.coarse[i].day).toBe(axis.coarse[i - 1].day + axis.coarse[i - 1].days);
    }
    const last = axis.coarse[axis.coarse.length - 1];
    expect(last.day + last.days).toBe(120);
  });
});

describe("canvasWidthFor", () => {
  it("fills the pane when the content is narrower, else uses the content width", () => {
    // 10 days @ 10px/day = 100px of content, in a 400px pane → stretch to fill 400
    expect(canvasWidthFor(10, 10, 400)).toBe(400);
    // 60 days @ 10px/day = 600px of content, in a 400px pane → 600 (scrolls)
    expect(canvasWidthFor(60, 10, 400)).toBe(600);
    // an unmeasured pane (0) never shrinks the content below its natural width
    expect(canvasWidthFor(10, 10, 0)).toBe(100);
  });
});

describe("slider ↔ ppd mapping", () => {
  it("puts the month anchor at 0 and the day anchor at 1, log-scaled and monotonic", () => {
    expect(sliderToPpd(0)).toBeCloseTo(PPD_ANCHORS.month);
    expect(sliderToPpd(1)).toBeCloseTo(PPD_ANCHORS.day);
    expect(sliderToPpd(0.3)).toBeLessThan(sliderToPpd(0.7));
    expect(ppdToSlider(PPD_ANCHORS.month)).toBeCloseTo(0);
    expect(ppdToSlider(PPD_ANCHORS.day)).toBeCloseTo(1);
  });
  it("round-trips a slider position back to itself", () => {
    for (const pos of [0.1, 0.42, 0.75]) {
      expect(ppdToSlider(sliderToPpd(pos))).toBeCloseTo(pos);
    }
  });
  it("clamps an out-of-track position to the anchor densities", () => {
    expect(sliderToPpd(-0.5)).toBeCloseTo(PPD_ANCHORS.month);
    expect(sliderToPpd(1.5)).toBeCloseTo(PPD_ANCHORS.day);
  });
});

describe("clampPpd", () => {
  it("holds ppd between the month (min) and day (max) anchors", () => {
    expect(clampPpd(PPD_ANCHORS.week)).toBe(PPD_ANCHORS.week);
    expect(clampPpd(1000)).toBe(PPD_ANCHORS.day); // never zoom past the day anchor
    expect(clampPpd(0.1)).toBe(PPD_ANCHORS.month); // never zoom out past the month anchor
  });
});

describe("visibleDaysFor", () => {
  it("counts the day-columns spanning the canvas, rounding up, at least one", () => {
    // a 400px canvas at 10px/day shows 40 days of grid (fills past short data)
    expect(visibleDaysFor(400, 10)).toBe(40);
    // partial columns round up so the grid always reaches the canvas edge
    expect(visibleDaysFor(405, 10)).toBe(41);
    // never zero, even for a degenerate canvas
    expect(visibleDaysFor(0, 10)).toBe(1);
  });
});

describe("weekStart", () => {
  it("returns the week's first day — the Monday on/before a date — for a monday-start week", () => {
    expect(weekStart("2026-12-28", "monday")).toBe("2026-12-28"); // a Monday → itself
    expect(weekStart("2026-12-31", "monday")).toBe("2026-12-28"); // Thu → back to Monday
    expect(weekStart("2027-01-03", "monday")).toBe("2026-12-28"); // Sun → same week's Monday
  });
});

describe("weekNumberOf", () => {
  it("numbers a mid-year date from the week containing Jan 1 (jan1 anchor, yearly reset)", () => {
    // 2026-07-01 is in the week starting Mon 2026-06-29; 2026 W01 starts Mon
    // 2025-12-29 (the week holding Thu 2026-01-01) → 27 weeks on.
    expect(weekNumberOf("2026-07-01", WW, "2026-08-01")).toEqual({ year: 2026, week: 27 });
  });
});

describe("weekNumberOf — by_today cross-year week", () => {
  // The physical week Mon 2026-12-28 … Sun 2027-01-03 holds both Dec 31 2026 and
  // Jan 1 2027, so it is simultaneously 2026's last week and 2027's W01.
  const overlap = "2026-12-31";

  it("reads as the OLD year's last week while today is still before that New Year", () => {
    expect(weekNumberOf(overlap, WW, "2026-06-01")).toEqual({ year: 2026, week: 53 });
    expect(weekNumberOf(overlap, WW, "2026-12-31")).toEqual({ year: 2026, week: 53 });
  });

  it("reads as the NEW year's W01 once today is on/after that New Year", () => {
    expect(weekNumberOf(overlap, WW, "2027-01-01")).toEqual({ year: 2027, week: 1 });
    expect(weekNumberOf(overlap, WW, "2027-06-01")).toEqual({ year: 2027, week: 1 });
  });

  it("applies per-crossing: a PAST crossing reads new, a FUTURE crossing reads old (from one 'today')", () => {
    // today = mid-2027: the 2026→2027 crossing is past (new: 2027 W01) …
    expect(weekNumberOf("2026-12-31", WW, "2027-06-01")).toEqual({ year: 2027, week: 1 });
    // … while the 2027→2028 crossing (week holding 2028-01-01) is still future (old: 2027 W53).
    expect(weekNumberOf("2027-12-31", WW, "2027-06-01")).toEqual({ year: 2027, week: 53 });
  });
});

describe("weekNumberOf — static boundary modes", () => {
  const NEW: WeekRule = { ...WW, boundary: "new_year" };
  const OLD: WeekRule = { ...WW, boundary: "old_year" };

  it("new_year: the cross-year week is the new year's W01, and its numbering carries on (first full week = W02)", () => {
    expect(weekNumberOf("2026-12-31", NEW, "2000-01-01")).toEqual({ year: 2027, week: 1 });
    expect(weekNumberOf("2027-01-04", NEW, "2000-01-01")).toEqual({ year: 2027, week: 2 });
  });

  it("old_year: the cross-year week is the old year's LAST week (52 here — both years first-full-anchored), and the new year's W01 slips to the first full week", () => {
    // Pure old_year anchors EVERY year at its first full week, so 2026 too runs
    // W01=2026-01-05 … its last (the cross-year week) = W52. (The W53 you see in
    // by_today comes from 2026 staying jan1-anchored while 2027 is first-full.)
    expect(weekNumberOf("2026-12-31", OLD, "2099-01-01")).toEqual({ year: 2026, week: 52 });
    expect(weekNumberOf("2027-01-04", OLD, "2099-01-01")).toEqual({ year: 2027, week: 1 });
  });
});

describe("formatWeekLabel", () => {
  it("renders the user's W{y1}{ww} format (year's last digit + zero-padded week)", () => {
    expect(formatWeekLabel({ year: 2026, week: 1 }, "W{y1}{ww}")).toBe("W601");
    expect(formatWeekLabel({ year: 2027, week: 44 }, "W{y1}{ww}")).toBe("W744");
  });
  it("supports the full token set and defaults to {yyyy}-W{ww}", () => {
    expect(formatWeekLabel({ year: 2026, week: 5 }, "{yyyy}-W{ww}")).toBe("2026-W05");
    expect(formatWeekLabel({ year: 2026, week: 5 }, "{yy}W{w}")).toBe("26W5");
    expect(formatWeekLabel({ year: 2026, week: 5 })).toBe("2026-W05");
  });
});

describe("weekLabelOf (whole pipeline → the string the axis shows)", () => {
  it("renders a plain week as W{y1}{ww}", () => {
    expect(weekLabelOf("2026-07-01", WW, "2026-08-01")).toBe("W627");
  });
  it("shows the cross-year week as W653 before its New Year, W701 on/after — the exact case the user described", () => {
    expect(weekLabelOf("2026-12-31", WW, "2026-06-01")).toBe("W653");
    expect(weekLabelOf("2026-12-31", WW, "2027-06-01")).toBe("W701");
  });
});

describe("weekNumberOf — other documented knobs are honored", () => {
  it("first_week:first_full anchors on the first whole week (≠ jan1)", () => {
    // 2026 opens Thu, so its first full week starts Mon 2026-01-05 → one behind jan1.
    expect(weekNumberOf("2026-07-01", { start: "monday", first_week: "first_full" }, "2026-08-01")).toEqual({ year: 2026, week: 26 });
    expect(weekNumberOf("2026-07-01", { start: "monday", first_week: "jan1" }, "2026-08-01")).toEqual({ year: 2026, week: 27 });
  });

  it("first_week:iso is the ISO-8601 week (first-Thursday), so early-Jan can roll into the prior ISO year", () => {
    // Sat 2027-01-02 is in the week holding Jan 1 2027; ISO puts that week in 2026
    // (a 53-week ISO year), whereas jan1 calls it 2027 W01.
    expect(weekNumberOf("2027-01-02", { start: "monday", first_week: "iso" }, "2027-06-01")).toEqual({ year: 2026, week: 53 });
    expect(weekNumberOf("2027-01-02", { start: "monday", first_week: "jan1" }, "2027-06-01")).toEqual({ year: 2027, week: 1 });
  });

  it("reset:none counts continuously from epoch (never rolls back to W01)", () => {
    const cont: WeekRule = { start: "monday", reset: "none", epoch: "2026-01-01" };
    expect(weekNumberOf("2026-01-12", cont, "2026-01-12")).toEqual({ year: 2026, week: 3 });
    expect(weekNumberOf("2027-01-11", cont, "2027-01-11")).toEqual({ year: 2027, week: 55 });
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
