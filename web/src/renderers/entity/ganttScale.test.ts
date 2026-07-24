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
} from "./ganttScale";

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
