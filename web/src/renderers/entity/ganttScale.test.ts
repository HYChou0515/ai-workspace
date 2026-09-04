import { describe, expect, it } from "vitest";

import {
  applyDrag,
  AXIS_DAY_OF_MONTH_PX,
  AXIS_MIN_LABEL_PX,
  AXIS_WEEKDAY_PX,
  axisFor,
  canvasWidthFor,
  clampPpd,
  columnPx,
  daysBetween,
  fitPpd,
  grainFor,
  PPD_HOUR_GRAIN,
  PPD_MAX_FIT,
  deltaDays,
  PPD_ANCHORS,
  ppdToSlider,
  resolveSpan,
  pxPerDay,
  shiftDate,
  sliderToPpd,
  spanToDates,
  unionSpan,
  visibleDaysFor,
  barColumns,
  columnOf,
  dateAtColumn,
  formatWeekLabel,
  instantOf,
  isWeekend,
  shiftWorkingDays,
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

  it("keeps the time of day when the span names one (#785)", () => {
    // The parser already read these — `Date.parse` accepts them — and then threw
    // the clock away on the way out, so "09:30 to 17:00" was stored, reloaded,
    // and silently redrawn as two whole days. Minutes are the stated precision.
    expect(spanToDates("2026-01-05T09:30/2026-01-05T17:00")).toEqual({
      start: "2026-01-05T09:30",
      end: "2026-01-05T17:00",
    });
  });

  it("reads a clock with no zone as UTC, like every other date in this module", () => {
    // ES parses a zone-less date-TIME as LOCAL and a zone-less DATE as UTC, so
    // taking the default would put the two halves of one span in two different
    // calendars — and a 23:30 bar would jump a day for anyone east of London.
    expect(spanToDates("2026-01-05T23:30/2026-01-06")).toEqual({
      start: "2026-01-05T23:30",
      end: "2026-01-06",
    });
    expect(spanToDates("2026-01-05T09:30:45.123Z/2026-01-05T17:00:00Z")).toEqual({
      start: "2026-01-05T09:30",
      end: "2026-01-05T17:00",
    });
  });

  it("leaves a plain date plain, so nothing already on screen moves", () => {
    // The whole of P2 rests on this: a plain-date span takes the identical path
    // it always did, so the chart is pixel-for-pixel what it was.
    expect(spanToDates("2026-01-10/2026-01-20")).toEqual({
      start: "2026-01-10",
      end: "2026-01-20",
    });
  });
});

describe("a span that names a clock still lands on the right day (#785)", () => {
  // P2 keeps the chart day-granular on purpose — the columns get finer in P3.
  // What has to hold NOW is that carrying a clock through the parser cannot
  // break the day arithmetic: every one of these used to be handed a bare
  // `YYYY-MM-DD` and would read `2026-01-05T09:30` as a local-time instant or,
  // in `weekdayOf`'s case, as an Invalid Date.
  it("measures the same columns as the plain-date span over those days", () => {
    expect(columnOf("2026-01-05T09:30", "2026-01-07T17:00", false)).toBe(
      columnOf("2026-01-05", "2026-01-07", false),
    );
    expect(barColumns({ start: "2026-01-05T09:30", end: "2026-01-07T17:00" }, false)).toBe(3);
  });

  it("counts DAYS, not rounded elapsed time — 01:00 to 23:00 next day is one day", () => {
    // The measurement it replaces divided elapsed ms by a day and rounded, so a
    // 46-hour span read as two days and an 11-hour one as none. Stated against
    // the plain-date answer so it means the same thing in any TZ the suite runs
    // in — which is also the bug: a zone-less clock parses as LOCAL.
    expect(daysBetween("2026-01-05T01:00", "2026-01-06T23:00")).toBe(
      daysBetween("2026-01-05", "2026-01-06"),
    );
    expect(columnOf("2026-01-05T01:00", "2026-01-06T23:00", false)).toBe(1);
  });

  it("knows a Saturday afternoon is still a weekend", () => {
    expect(isWeekend("2026-01-10T14:00")).toBe(true);
  });

  it("counts working columns across a weekend from the clock's own day", () => {
    // Fri 09:30 → Mon 17:00 is two working columns apart, weekend collapsed.
    expect(columnOf("2026-01-09T09:30", "2026-01-12T17:00", true)).toBe(1);
  });

  it("shifts and measures from the day, not from an unparseable string", () => {
    expect(shiftDate("2026-01-05T09:30", 1)).toBe("2026-01-06");
    expect(daysBetween("2026-01-05T09:30", "2026-01-07T17:00")).toBe(2);
    expect(weekStart("2026-01-07T17:00")).toBe("2026-01-05");
  });

  it("does not lose the clock when the bar is dragged", () => {
    // Dragging measures in whole days here (P3 makes it finer), so a move is a
    // change of DAY — the time of day is not the drag's to throw away. Every
    // shift helper returns a bare date, so without this a single drag silently
    // flattened "09:30–17:00" into a two-day bar.
    expect(applyDrag({ start: "2026-01-05T09:30", end: "2026-01-05T17:00" }, "move", 1, false)).toEqual(
      { start: "2026-01-06T09:30", end: "2026-01-06T17:00" },
    );
    expect(applyDrag({ start: "2026-01-05T09:30", end: "2026-01-07T17:00" }, "end", -1, false)).toEqual(
      { start: "2026-01-05T09:30", end: "2026-01-06T17:00" },
    );
  });

  it("names a column with a bare date even when the origin carries a clock", () => {
    // `dateAtColumn` is `columnOf`'s inverse and its answers become dates the
    // axis and the drag then shift. Column 0 of a timed origin is the only case
    // that reaches the return without passing through a shift, so it is the one
    // that could hand back a clock where the rest of the module expects a day.
    expect(dateAtColumn("2026-01-05T09:30", 0, true)).toBe("2026-01-05");
    expect(dateAtColumn("2026-01-05T09:30", 2, false)).toBe("2026-01-07");
  });

  it("still writes plain dates for a plain-date bar", () => {
    expect(applyDrag({ start: "2026-01-05", end: "2026-01-07" }, "move", 1, false)).toEqual({
      start: "2026-01-06",
      end: "2026-01-08",
    });
  });
});

describe("hour columns (#785)", () => {
  const HOURS = { grain: "hour", skipWeekends: false } as const;
  const HOURS_SKIP = { grain: "hour", skipWeekends: true } as const;

  it("turns one day column into twenty-four", () => {
    expect(columnOf("2026-01-05", "2026-01-06", HOURS)).toBe(24);
    expect(columnOf("2026-01-05T09:00", "2026-01-05T17:00", HOURS)).toBe(8);
  });

  it("still reads the bare boolean as day columns, which is what it always meant", () => {
    expect(columnOf("2026-01-05", "2026-01-06", false)).toBe(1);
    expect(columnOf("2026-01-05", "2026-01-06", { grain: "day", skipWeekends: false })).toBe(1);
  });

  it("gives a weekend no hours either", () => {
    // Fri 09:00 → Mon 17:00: fifteen hours left of Friday, then Monday's
    // seventeen. The weekend is 48 hours of nothing at both grains.
    expect(columnOf("2026-01-09T09:00", "2026-01-12T17:00", HOURS_SKIP)).toBe(32);
  });

  it("collapses a weekend origin onto the next working hour, as the day grain does", () => {
    // The day grain already puts Sat, Sun and Monday on one column; measuring
    // from the Saturday's clock instead would make the two grains disagree —
    // and it was exactly that disagreement that froze a tab in #690.
    expect(columnOf("2026-01-10T10:00", "2026-01-12T05:00", HOURS_SKIP)).toBe(5);
  });

  it("places minutes inside their hour rather than snapping them away", () => {
    // Spans are specified to the minute; the CHART is specified to the hour.
    // A half-hour is half a column, not a rounding error.
    expect(columnOf("2026-01-05T09:00", "2026-01-05T09:30", HOURS)).toBe(0.5);
  });

  it("is signed, like the day grain", () => {
    expect(columnOf("2026-01-06", "2026-01-05", HOURS)).toBe(-24);
  });

  it("names the instant at an hour column, and is columnOf's inverse there", () => {
    expect(dateAtColumn("2026-01-05", 9, HOURS)).toBe("2026-01-05T09:00");
    expect(dateAtColumn("2026-01-05", 24, HOURS)).toBe("2026-01-06T00:00");
    expect(dateAtColumn("2026-01-05", -1, HOURS)).toBe("2026-01-04T23:00");
    for (const col of [0, 1, 30, 47, 168]) {
      expect(columnOf("2026-01-05", dateAtColumn("2026-01-05", col, HOURS), HOURS)).toBe(col);
    }
  });

  it("walks over the weekend at hour grain, so the round trip holds there too", () => {
    // Fri 00:00 plus a working day's worth of hours is Monday, not Saturday.
    expect(dateAtColumn("2026-01-09", 24, HOURS_SKIP)).toBe("2026-01-12T00:00");
    for (const col of [0, 5, 24, 40, 120]) {
      expect(columnOf("2026-01-09", dateAtColumn("2026-01-09", col, HOURS_SKIP), HOURS_SKIP)).toBe(
        col,
      );
    }
  });

  it("measures a bar in hours, from its start to the moment it ends", () => {
    expect(barColumns({ start: "2026-01-05", end: "2026-01-05" }, HOURS)).toBe(24);
    expect(barColumns({ start: "2026-01-05", end: "2026-01-06" }, HOURS)).toBe(48);
    expect(barColumns({ start: "2026-01-05T09:30", end: "2026-01-05T17:00" }, HOURS)).toBe(7.5);
  });

  it("stops a bar at the weekend rather than drawing through it", () => {
    // Friday 09:00 to the end of Friday is fifteen hours; the Saturday and
    // Sunday the plain end date runs up to are worth nothing.
    expect(barColumns({ start: "2026-01-09T09:00", end: "2026-01-09" }, HOURS_SKIP)).toBe(15);
  });

  it("still counts whole days at day grain, inclusive as it always was", () => {
    expect(barColumns({ start: "2026-07-13", end: "2026-07-15" }, false)).toBe(3);
  });

  it("keeps a bar's length when it is dragged at hour grain", () => {
    // The trap: a plain end date denotes the midnight it runs UP TO, so
    // shifting it as though it were that day's 00:00 leaves the instant it
    // denotes exactly where it was — and the bar loses a day per drag.
    const span = { start: "2026-01-05", end: "2026-01-07" };
    const moved = applyDrag(span, "move", 24, HOURS);
    expect(barColumns(moved, HOURS)).toBe(barColumns(span, HOURS));
    expect(moved.start).toBe("2026-01-06T00:00");
  });

  it("nudges one end by hours without moving the other", () => {
    expect(applyDrag({ start: "2026-01-05T09:00", end: "2026-01-05T17:00" }, "end", 2, HOURS)).toEqual(
      { start: "2026-01-05T09:00", end: "2026-01-05T19:00" },
    );
  });

  it("will not let a resize invert the bar at hour grain", () => {
    const flat = applyDrag({ start: "2026-01-05T09:00", end: "2026-01-05T17:00" }, "end", -20, HOURS);
    expect(instantOf(flat.end, "end")).toBeGreaterThanOrEqual(instantOf(flat.start, "start"));
  });

  it("carries the origin's own clock into the answer", () => {
    // The origin is the chart's left edge, which is a record's start — it can
    // be half past nine as easily as midnight.
    expect(dateAtColumn("2026-01-05T09:30", 2, HOURS)).toBe("2026-01-05T11:30");
    expect(dateAtColumn("2026-01-05T09:30", 15, HOURS)).toBe("2026-01-06T00:30");
  });
});

describe("the slider reaches hours (#785)", () => {
  it("keeps days until the density can actually show an hour", () => {
    expect(grainFor(PPD_ANCHORS.month)).toBe("day");
    expect(grainFor(PPD_ANCHORS.day)).toBe("day");
    expect(grainFor(PPD_HOUR_GRAIN - 1)).toBe("day");
    expect(grainFor(PPD_HOUR_GRAIN)).toBe("hour");
  });

  it("extends the track far enough to reach hours, anchors still inside it", () => {
    expect(sliderToPpd(1)).toBeGreaterThanOrEqual(PPD_HOUR_GRAIN);
    expect(sliderToPpd(0)).toBeLessThan(PPD_ANCHORS.month);
    expect(sliderToPpd(1)).toBeGreaterThan(PPD_ANCHORS.day);
  });

  it("crosses the threshold without moving anything on screen", () => {
    // A day is one column at day grain and twenty-four at hour grain, and an
    // hour column is a twenty-fourth as wide — so the same date sits at the
    // same x on both sides of the switch. Zooming must not teleport the chart,
    // and building the hour count ON the day count is what guarantees it.
    const ppd = PPD_HOUR_GRAIN;
    const at = (grain: "day" | "hour") =>
      columnOf("2026-01-05", "2026-01-08", { grain, skipWeekends: false }) * columnPx(ppd, grain);
    expect(at("hour")).toBeCloseTo(at("day"));
  });

  it("never opens a short project in hours — fitting to the pane stops at days", () => {
    // Two days in a 900px pane fits at 450 px/day, which is well past the
    // threshold. Going finer than days is a deliberate drag, never something a
    // project gets for being short.
    expect(fitPpd(900, 2)).toBe(PPD_MAX_FIT);
    expect(grainFor(fitPpd(900, 2))).toBe("day");
    // And fitting still fits whenever fitting is the smaller number.
    expect(fitPpd(900, 90)).toBeCloseTo(10);
  });
});

describe("non-working hours take no width (#785)", () => {
  const WORK = { grain: "hour", skipWeekends: false, work: { from: 7, to: 21 } } as const;
  const WORK_SKIP = { grain: "hour", skipWeekends: true, work: { from: 7, to: 21 } } as const;

  it("makes a day fourteen columns instead of twenty-four", () => {
    expect(barColumns({ start: "2026-01-05", end: "2026-01-05" }, WORK)).toBe(14);
  });

  it("gives an overnight gap no width at all", () => {
    // 22:00 to 05:00 is seven hours of clock and no working time — the same
    // thing a weekend is, one scale down.
    expect(columnOf("2026-01-05T22:00", "2026-01-06T05:00", WORK)).toBe(0);
  });

  it("counts the working end of an evening and the working start of a morning", () => {
    expect(columnOf("2026-01-05T20:00", "2026-01-06T08:00", WORK)).toBe(2);
    expect(barColumns({ start: "2026-01-05T20:00", end: "2026-01-06T08:00" }, WORK)).toBe(2);
  });

  it("changes nothing at day grain — a day is still one column", () => {
    const dayWithWindow = { grain: "day", skipWeekends: true, work: { from: 7, to: 21 } } as const;
    expect(columnOf("2026-01-05", "2026-01-09", dayWithWindow)).toBe(
      columnOf("2026-01-05", "2026-01-09", true),
    );
    expect(barColumns({ start: "2026-01-05", end: "2026-01-09" }, dayWithWindow)).toBe(5);
  });

  it("walks the weekend and the nights through the same machinery", () => {
    // Fri 20:00 → Mon 08:00: one working hour left on Friday, one done on
    // Monday, and everything between them is time the chart does not draw.
    expect(columnOf("2026-01-09T20:00", "2026-01-12T08:00", WORK_SKIP)).toBe(2);
  });

  it("stays its own inverse, and opens column zero at the start of the working day", () => {
    expect(dateAtColumn("2026-01-05", 0, WORK)).toBe("2026-01-05T07:00");
    expect(dateAtColumn("2026-01-05", 14, WORK)).toBe("2026-01-06T07:00");
    for (const col of [0, 1, 14, 20, 42]) {
      expect(columnOf("2026-01-05", dateAtColumn("2026-01-05", col, WORK), WORK)).toBe(col);
    }
  });

  it("gives the axis a band per working day, fourteen columns wide", () => {
    const axis = axisFor("2026-01-05", 28, PPD_HOUR_GRAIN * 2, undefined, "", false, {}, {
      from: 7,
      to: 21,
    });
    expect(axis.coarse.map((b) => [b.day, b.days])).toEqual([
      [0, 14],
      [14, 14],
    ]);
    expect(axis.fine.map((t) => t.label)).not.toContain("03");
  });
});

describe("the axis at hour grain (#785)", () => {
  const PPD = PPD_HOUR_GRAIN * 2; // 12px hour columns

  it("labels hours on the fine row and names the day in the band above", () => {
    const axis = axisFor("2026-01-05", 48, PPD);
    expect(axis.unit).toBe("hour");
    expect(axis.fine.map((t) => t.label)).toContain("09");
    expect(axis.coarse.map((b) => b.label)).toContain("Mon 5 Jan");
  });

  it("gives each day a band exactly twenty-four columns wide", () => {
    const axis = axisFor("2026-01-05", 48, PPD);
    expect(axis.coarse[0]).toEqual({ day: 0, days: 24, label: "Mon 5 Jan" });
    expect(axis.coarse[1].day).toBe(24);
  });

  it("never spaces two hour labels closer than the min label width", () => {
    // The same rule the day axis has had since #448 — a row of labels that can
    // collide is the bug this whole tier system exists to prevent.
    const px = columnPx(PPD_HOUR_GRAIN, "hour");
    const axis = axisFor("2026-01-05", 72, PPD_HOUR_GRAIN);
    expect(axis.fine.length).toBeGreaterThan(1);
    for (let i = 1; i < axis.fine.length; i++) {
      expect((axis.fine[i].day - axis.fine[i - 1].day) * px).toBeGreaterThanOrEqual(
        AXIS_MIN_LABEL_PX,
      );
    }
  });

  it("puts its labels on the hour even when the chart starts mid-morning", () => {
    // The chart's left edge is a record's start, which is as likely to be 09:30
    // as midnight. Labels running :30 past every hour would read as broken.
    const axis = axisFor("2026-01-05T09:30", 24, PPD);
    for (const t of axis.fine) expect(t.label).toMatch(/^\d\d$/);
    expect(axis.fine[0].day).toBeCloseTo(0.5);
  });

  it("skips the weekend on the hour axis too", () => {
    // 48 hour columns from Friday is Friday and Monday — Saturday and Sunday
    // are worth no columns at either grain.
    const axis = axisFor("2026-01-09", 48, PPD, undefined, "", true);
    expect(axis.coarse.map((b) => b.label)).toEqual(["Fri 9 Jan", "Mon 12 Jan"]);
  });
});

describe("a bar is as wide as the work in it (#785)", () => {
  const WORK = { grain: "hour", skipWeekends: false, work: { from: 7, to: 21 } } as const;

  it("gives a weekend-only task no columns instead of a full working day", () => {
    // Sat → Sun with weekends collapsed is zero working time, and the `+1`
    // floor drew it exactly as wide as a Monday task. That is not a rounding
    // choice — it is the chart stating something untrue about the schedule.
    expect(barColumns({ start: "2026-01-10", end: "2026-01-11" }, true)).toBe(0);
    // A real working day is still one column, which is the control.
    expect(barColumns({ start: "2026-01-12", end: "2026-01-12" }, true)).toBe(1);
  });

  it("counts a Saturday-to-Monday task as the one working day it contains", () => {
    expect(barColumns({ start: "2026-01-10", end: "2026-01-12" }, true)).toBe(1);
  });

  it("gives a task that falls entirely after hours no columns either", () => {
    expect(barColumns({ start: "2026-01-05T22:00", end: "2026-01-05T23:00" }, WORK)).toBe(0);
  });

  it("still occupies a whole day column when it only uses part of the day", () => {
    // At day grain the column IS the unit — an eight-hour task happens on that
    // day, so it is drawn on it. Only the finer grain can say how much of it.
    expect(barColumns({ start: "2026-01-05T09:00", end: "2026-01-05T17:00" }, false)).toBe(1);
  });
});

describe("resolveSpan — every record gets a bar (#785)", () => {
  const TODAY = "2026-01-05";

  it("passes a fully stated span through untouched, and says so", () => {
    expect(resolveSpan("2026-03-01/2026-03-10", TODAY)).toEqual({
      span: { start: "2026-03-01", end: "2026-03-10" },
      source: "given",
    });
  });

  it("proposes a week from today when nothing is stated", () => {
    // Leaving the record off the chart does not say "no dates yet" — it says
    // nothing at all, which reads as no such work. A proposal can at least be
    // seen, argued with, and dragged into place.
    expect(resolveSpan(undefined, TODAY)).toEqual({
      span: { start: "2026-01-05", end: "2026-01-11" },
      source: "derived",
    });
    expect(resolveSpan("", TODAY).source).toBe("derived");
  });

  it("computes the missing end from the stated start, a week out", () => {
    expect(resolveSpan("2026-03-01/", TODAY)).toEqual({
      span: { start: "2026-03-01", end: "2026-03-07" },
      source: "derived",
    });
  });

  it("computes the missing start backwards from the stated end", () => {
    expect(resolveSpan("/2026-03-10", TODAY)).toEqual({
      span: { start: "2026-03-04", end: "2026-03-10" },
      source: "derived",
    });
  });

  it("keeps the clock on the end it was given", () => {
    expect(resolveSpan("2026-03-01T09:30/", TODAY).span).toEqual({
      start: "2026-03-01T09:30",
      end: "2026-03-07T09:30",
    });
  });

  it("proposes rather than vanishes when the range is back to front", () => {
    // A reversed range used to make the record disappear, which is the same
    // silence as having no dates at all — and the same answer serves both.
    expect(resolveSpan("2026-03-10/2026-03-01", TODAY)).toEqual({
      span: { start: "2026-01-05", end: "2026-01-11" },
      source: "derived",
    });
  });

  it("proposes a whole week — seven days, because a plain end date is inclusive", () => {
    const { span } = resolveSpan(undefined, TODAY);
    expect(barColumns(span, false)).toBe(7);
  });
});

describe("unionSpan (#785)", () => {
  it("reaches from the earliest start to the latest end", () => {
    expect(
      unionSpan([
        { start: "2026-03-05", end: "2026-03-10" },
        { start: "2026-03-01", end: "2026-03-04" },
        { start: "2026-03-02", end: "2026-03-20" },
      ]),
    ).toEqual({ start: "2026-03-01", end: "2026-03-20" });
  });

  it("compares what the edges MEAN, not how they are written", () => {
    // A plain end date runs to the next midnight, so it reaches FURTHER than a
    // 17:00 on the same day — though as text it sorts earlier, being shorter.
    expect(
      unionSpan([
        { start: "2026-03-01T09:00", end: "2026-03-01T17:00" },
        { start: "2026-03-01", end: "2026-03-01" },
      ]),
    ).toEqual({ start: "2026-03-01", end: "2026-03-01" });
  });

  it("keeps the edges as they were written, rather than rewriting them", () => {
    // The union is a DRAWING. Nothing about it should look like a decision
    // someone made about a record's dates.
    expect(
      unionSpan([
        { start: "2026-03-01T09:00", end: "2026-03-02T17:00" },
        { start: "2026-03-05", end: "2026-03-06" },
      ]),
    ).toEqual({ start: "2026-03-01T09:00", end: "2026-03-06" });
  });

  it("is nothing over nothing", () => {
    expect(unionSpan([])).toBeNull();
  });
});

describe("instantOf (#785)", () => {
  it("reads a plain date as the WHOLE day, bounded by the next midnight", () => {
    // The bound is EXCLUSIVE: the last minute you can be inside a plain date is
    // 23:59, and the interval it names runs up to — not through — 00:00 the
    // next day. Stated as the bound rather than as that last minute because
    // every width in the chart is then just `end - start`; pinning it to 23:59
    // makes each one need a "+ one more minute", which is the same fudge as the
    // day grain's `+1` that P6 exists to delete.
    expect(instantOf("2026-01-05", "start")).toBe(Date.parse("2026-01-05T00:00:00Z"));
    expect(instantOf("2026-01-05", "end")).toBe(Date.parse("2026-01-06T00:00:00Z"));
  });

  it("reads a clock as exactly that minute — the moment work stops", () => {
    // "09:30–17:00" is seven and a half hours to everyone who writes it, so a
    // timed end is the bound itself, not the last minute worked.
    expect(instantOf("2026-01-05T09:30", "start")).toBe(Date.parse("2026-01-05T09:30:00Z"));
    expect(instantOf("2026-01-05T17:00", "end")).toBe(Date.parse("2026-01-05T17:00:00Z"));
  });

  it("makes a one-day span last exactly a day, which is what the +1 was patching", () => {
    const oneDay = instantOf("2026-01-05", "end") - instantOf("2026-01-05", "start");
    expect(oneDay).toBe(24 * 60 * 60_000);
    const morning =
      instantOf("2026-01-05T17:00", "end") - instantOf("2026-01-05T09:30", "start");
    expect(morning).toBe(7.5 * 60 * 60_000);
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

  it("with a week rule, the WEEK-density fine row shows week codes at week starts, over month bands", () => {
    // 2026-06-29 is a Monday → day 0 is a week start; at the week anchor every
    // week (7·10px) clears the label width, so ticks are one week apart.
    const axis = axisFor("2026-06-29", 60, PPD_ANCHORS.week, WW, "2026-08-01");
    expect(axis.unit).toBe("week");
    expect(axis.fine.every((t) => /^W\d{3}$/.test(t.label))).toBe(true);
    expect(axis.fine[0]).toEqual({ day: 0, label: "W627" });
    expect(axis.fine[1].day).toBe(7);
    expect(axis.coarse.map((b) => b.label)).toContain("Jul 2026");
  });

  it("zoomed IN (day density), a week rule shows the days of the week, not week codes — the week↔date transition", () => {
    // #690 P8 SUPERSEDED what these digits mean. They used to be days of the
    // MONTH thinned to fit; the ask was an axis whose subject is the week, so
    // at this density they are now every day, numbered WITHIN the week. Still
    // one-or-two digits, still not week codes — the transition this test was
    // written for is intact; what changed is the requirement behind it.
    const axis = axisFor("2026-06-29", 40, PPD_ANCHORS.day, WW, "2026-08-01");
    expect(axis.unit).toBe("day");
    expect(axis.fine.every((t) => /^\d{1,2}$/.test(t.label))).toBe(true);
  });

  it("week-code fine labels never overlap at week/month density (thinned to whole weeks)", () => {
    for (const ppd of [5, 7, 10, 14]) {
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

  it("a week rule with no explicit start still ticks on Monday weeks in the axis", () => {
    const axis = axisFor("2026-06-29", 20, PPD_ANCHORS.week, { label: "W{y1}{ww}" }, "2026-08-01");
    expect(axis.fine[0]).toEqual({ day: 0, label: "W627" });
  });

  it("zoomed out: a start date in December advances the first month tick into the next year", () => {
    const axis = axisFor("2026-12-15", 120, PPD_ANCHORS.month);
    expect(axis.unit).toBe("month");
    expect(axis.coarse.map((b) => b.label)).toEqual(expect.arrayContaining(["2026", "2027"]));
    expect(axis.fine.some((t) => t.label === "Jan")).toBe(true); // first whole month = Jan 2027
  });

  it("with skip-weekends, week-code ticks are one WORKING week (5 columns) apart, not 7", () => {
    const skip = axisFor("2026-06-29", 60, PPD_ANCHORS.week, WW, "2026-08-01", true);
    const cal = axisFor("2026-06-29", 60, PPD_ANCHORS.week, WW, "2026-08-01", false);
    expect(skip.fine[0].label).toBe("W627");
    expect(skip.fine[1].day - skip.fine[0].day).toBe(5); // a working week
    expect(cal.fine[1].day - cal.fine[0].day).toBe(7); // a calendar week
  });

  it("with skip-weekends, day-density fine ticks land only on working days", () => {
    const axis = axisFor("2026-06-29", 30, PPD_ANCHORS.day, undefined, "", true);
    expect(axis.unit).toBe("day");
    expect(axis.fine.length).toBeGreaterThan(1);
    for (const t of axis.fine) expect(isWeekend(dateAtColumn("2026-06-29", t.day, true))).toBe(false);
  });

  it("with skip-weekends, the zoomed-out month axis still builds (months over year bands)", () => {
    const axis = axisFor("2026-01-01", 300, PPD_ANCHORS.month, undefined, "", true);
    expect(axis.unit).toBe("month");
    expect(axis.coarse.map((b) => b.label)).toEqual(expect.arrayContaining(["2026"]));
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

describe("axisFor — the week is the subject of the axis (#690 P8)", () => {
  const WW: WeekRule = { label: "W{y1}{ww}" }; // monday / jan1, as the views use
  const MON = "2026-06-29"; // a Monday, so column 0 opens a week

  it("at day density, the fine row is the days of the WEEK under week-code bands", () => {
    // The point of the redesign: at the densest zoom you read "which week, and
    // which day of it" — not a day-of-month number whose month is a band away.
    const axis = axisFor(MON, 14, PPD_ANCHORS.day, WW, "2026-08-01");
    expect(axis.fine.map((t) => t.label).slice(0, 9)).toEqual(["1", "2", "3", "4", "5", "6", "7", "1", "2"]);
    expect(axis.coarse[0].label).toBe("W627");
    expect(axis.coarse.map((b) => b.label)).toContain("W628");
  });

  it("every column is labelled at that density — no thinning, because a digit fits where a date did not", () => {
    const axis = axisFor(MON, 14, PPD_ANCHORS.day, WW, "2026-08-01");
    expect(axis.fine.map((t) => t.day)).toEqual([...Array(14).keys()]);
  });

  it("runs 1–5 when weekends are skipped and 1–7 when they are not", () => {
    const skip = axisFor(MON, 10, PPD_ANCHORS.day, WW, "2026-08-01", true);
    expect(skip.fine.map((t) => t.label)).toEqual(["1", "2", "3", "4", "5", "1", "2", "3", "4", "5"]);
    const cal = axisFor(MON, 7, PPD_ANCHORS.day, WW, "2026-08-01", false);
    expect(cal.fine.map((t) => t.label)).toEqual(["1", "2", "3", "4", "5", "6", "7"]);
  });

  it("counts from whatever day the week rule starts on, not from Monday", () => {
    // A sunday-start week must read 1 on Sunday, or the digits disagree with
    // the week code sitting above them.
    const sun = axisFor("2026-06-28", 7, PPD_ANCHORS.day, { ...WW, start: "sunday" }, "2026-08-01");
    expect(sun.fine[0].label).toBe("1"); // 2026-06-28 is a Sunday
    expect(sun.fine[1].label).toBe("2");
  });

  it("can spell the weekday out instead — which needs more room, so it engages later", () => {
    const named = axisFor(MON, 14, PPD_ANCHORS.day, WW, "2026-08-01", false, { weekday: "short" });
    expect(named.fine.slice(0, 3).map((t) => t.label)).toEqual(["Mon", "Tue", "Wed"]);
    // Between the two widths: digits still fit here, names do not, so the axis
    // falls back to week codes rather than overprinting.
    const tight = (AXIS_WEEKDAY_PX.number + AXIS_WEEKDAY_PX.short) / 2;
    expect(axisFor(MON, 40, tight, WW, "2026-08-01").fine[0].label).toBe("1");
    expect(axisFor(MON, 40, tight, WW, "2026-08-01", false, { weekday: "short" }).fine[0].label).toBe("W627");
  });

  it("shows the day of the month too, or only on hover, or not at all", () => {
    const hidden = axisFor(MON, 3, PPD_ANCHORS.day, WW, "2026-08-01");
    expect(hidden.fine[0].sub).toBeUndefined();
    expect(hidden.fine[0].title).toBeUndefined();

    const always = axisFor(MON, 3, PPD_ANCHORS.day, WW, "2026-08-01", false, { day_of_month: "always" });
    expect(always.fine.map((t) => t.sub)).toEqual(["29", "30", "1"]); // crosses into July
    expect(always.fine[0].title).toBe("2026-06-29"); // the whole date is free once you are hovering

    const hover = axisFor(MON, 3, PPD_ANCHORS.day, WW, "2026-08-01", false, { day_of_month: "hover" });
    expect(hover.fine.map((t) => t.sub)).toEqual([undefined, undefined, undefined]);
    expect(hover.fine[0].title).toBe("2026-06-29");
  });

  it("a second row of numbers needs room of its own — so it also engages later", () => {
    const tight = (AXIS_WEEKDAY_PX.number + AXIS_DAY_OF_MONTH_PX) / 2;
    expect(axisFor(MON, 40, tight, WW, "2026-08-01").fine[0].label).toBe("1");
    expect(axisFor(MON, 40, tight, WW, "2026-08-01", false, { day_of_month: "always" }).fine[0].label).toBe("W627");
  });

  it("week bands tile the window with no gaps and no band of zero width", () => {
    for (const [from, skip] of [[MON, false], ["2026-07-04", false], ["2026-07-04", true]] as const) {
      const axis = axisFor(from, 30, PPD_ANCHORS.day, WW, "2026-08-01", skip);
      expect(axis.coarse[0].day).toBe(0);
      for (const b of axis.coarse) expect(b.days).toBeGreaterThan(0);
      for (let i = 1; i < axis.coarse.length; i++) {
        expect(axis.coarse[i].day).toBe(axis.coarse[i - 1].day + axis.coarse[i - 1].days);
      }
      const last = axis.coarse.at(-1)!;
      expect(last.day + last.days).toBe(30);
    }
  });

  it("zoomed out one step it is week codes over months, exactly as before", () => {
    const axis = axisFor(MON, 60, PPD_ANCHORS.week, WW, "2026-08-01");
    expect(axis.unit).toBe("week");
    expect(axis.fine[0].label).toBe("W627");
    expect(axis.coarse.map((b) => b.label)).toContain("Jul 2026");
  });

  it("zoomed all the way out it drops to months over years", () => {
    const axis = axisFor("2026-01-01", 400, PPD_ANCHORS.month, WW, "2026-08-01");
    expect(axis.unit).toBe("month");
    expect(axis.fine.some((t) => t.label === "Feb")).toBe(true);
  });

  it("unless the view says to always show the week — then it keeps skip-labelling weeks", () => {
    // The setting only has anything to say at this end: the two denser tiers
    // are showing weeks already.
    const axis = axisFor("2026-01-01", 400, PPD_ANCHORS.month, WW, "2026-08-01", false, { always_week: true });
    expect(axis.unit).toBe("week");
    expect(axis.fine.every((t) => /^W\d{3}$/.test(t.label))).toBe(true);
    expect(axis.coarse.map((b) => b.label)).toEqual(expect.arrayContaining(["2026", "2027"]));
    // Thinned, or it would be a solid smear of week codes at 3px a day.
    for (let i = 1; i < axis.fine.length; i++) {
      expect((axis.fine[i].day - axis.fine[i - 1].day) * PPD_ANCHORS.month).toBeGreaterThanOrEqual(AXIS_MIN_LABEL_PX);
    }
  });

  it("changes nothing without a week rule — there is no week code to head the bands with", () => {
    const axis = axisFor(MON, 40, PPD_ANCHORS.day, undefined, "", false, { always_week: true, day_of_month: "always" });
    expect(axis.coarse.map((b) => b.label)).toContain("Jul 2026");
    expect(axis.fine.every((t) => t.sub === undefined)).toBe(true);
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
  it("travels PAST the named anchors — further in than day, further out than month", () => {
    // the ends of the track are beyond the anchors …
    expect(sliderToPpd(1)).toBeGreaterThan(PPD_ANCHORS.day); // zoom in past `day` (days widen)
    expect(sliderToPpd(0)).toBeLessThan(PPD_ANCHORS.month); // zoom out past `month` (months compress)
    // … and the three anchors sit strictly inside the track, in order.
    const [m, w, d] = [PPD_ANCHORS.month, PPD_ANCHORS.week, PPD_ANCHORS.day].map(ppdToSlider);
    expect(0).toBeLessThan(m);
    expect(m).toBeLessThan(w);
    expect(w).toBeLessThan(d);
    expect(d).toBeLessThan(1);
  });
  it("is log-scaled and monotonic", () => {
    expect(sliderToPpd(0.3)).toBeLessThan(sliderToPpd(0.7));
  });
  it("round-trips a slider position back to itself", () => {
    for (const pos of [0.1, 0.42, 0.75]) {
      expect(ppdToSlider(sliderToPpd(pos))).toBeCloseTo(pos);
    }
  });
  it("clamps an out-of-track position to the range ends", () => {
    expect(sliderToPpd(-0.5)).toBeCloseTo(sliderToPpd(0));
    expect(sliderToPpd(1.5)).toBeCloseTo(sliderToPpd(1));
  });
});

describe("clampPpd", () => {
  it("holds ppd inside the extended range (which runs past both anchors)", () => {
    expect(clampPpd(PPD_ANCHORS.week)).toBe(PPD_ANCHORS.week); // an anchor is untouched
    expect(clampPpd(PPD_ANCHORS.day + 10)).toBe(PPD_ANCHORS.day + 10); // past `day` is allowed
    expect(clampPpd(1000)).toBeGreaterThan(PPD_ANCHORS.day); // clamps to the (extended) max, past day
    expect(clampPpd(0.01)).toBeLessThan(PPD_ANCHORS.month); // clamps to the (extended) min, past month
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

describe("working-day columns (skip weekends)", () => {
  // 2026-06-29 is a Monday; that week runs Mon 06-29 … Fri 07-03, weekend 07-04/05,
  // next Mon 07-06.
  it("columnOf counts working days when skipping weekends (weekend collapses to the next working column)", () => {
    expect(columnOf("2026-06-29", "2026-06-29", true)).toBe(0);
    expect(columnOf("2026-06-29", "2026-07-03", true)).toBe(4); // Mon→Fri = 4 working steps
    expect(columnOf("2026-06-29", "2026-07-04", true)).toBe(5); // Sat collapses onto col 5
    expect(columnOf("2026-06-29", "2026-07-05", true)).toBe(5); // Sun too
    expect(columnOf("2026-06-29", "2026-07-06", true)).toBe(5); // next Mon shares that column
    expect(columnOf("2026-06-29", "2026-07-13", true)).toBe(10); // two weeks = 10 working days
    expect(columnOf("2026-07-06", "2026-06-29", true)).toBe(-5); // signed
  });
  it("columnOf is plain calendar days when NOT skipping", () => {
    expect(columnOf("2026-06-29", "2026-07-06", false)).toBe(7);
  });
  it("dateAtColumn is the inverse — the calendar date at a working-day column", () => {
    expect(dateAtColumn("2026-06-29", 4, true)).toBe("2026-07-03"); // Fri
    expect(dateAtColumn("2026-06-29", 5, true)).toBe("2026-07-06"); // skips the weekend to next Mon
    expect(dateAtColumn("2026-06-29", 10, true)).toBe("2026-07-13");
    expect(dateAtColumn("2026-06-29", 5, false)).toBe("2026-07-04"); // calendar
  });
  it("shiftWorkingDays hops over weekends", () => {
    expect(shiftWorkingDays("2026-07-03", 1, true)).toBe("2026-07-06"); // Fri +1 wd = Mon
    expect(shiftWorkingDays("2026-07-06", -1, true)).toBe("2026-07-03"); // Mon −1 wd = Fri
    expect(shiftWorkingDays("2026-07-03", 1, false)).toBe("2026-07-04"); // calendar
  });
  it("isWeekend flags Saturday and Sunday", () => {
    expect(isWeekend("2026-07-04")).toBe(true); // Sat
    expect(isWeekend("2026-07-05")).toBe(true); // Sun
    expect(isWeekend("2026-07-03")).toBe(false); // Fri
  });
});

describe("working-day columns — a weekend origin (found by #690 P8)", () => {
  // A chart's origin is the earliest date in the data, and nothing stops an
  // issue starting on a Saturday. With weekends skipped, that used to make
  // `columnOf` and `dateAtColumn` disagree by one — and `monthBands`, which
  // walks the window by asking one for a date and the other for a column, then
  // produced a band of zero width and looped FOREVER. Not a wrong label: a
  // frozen tab.
  const SAT = "2026-07-04"; // Saturday; Mon 07-06 is the working day it collapses onto

  it("dateAtColumn is the inverse of columnOf even from a weekend", () => {
    for (let col = 0; col < 12; col++) {
      expect(columnOf(SAT, dateAtColumn(SAT, col, true), true)).toBe(col);
    }
  });

  it("column 0 is the working day the weekend collapses onto, not the weekend itself", () => {
    // `columnOf` already says Sat, Sun and Monday are all column 0; asking for
    // the date AT column 0 has to give the same answer back.
    expect(dateAtColumn(SAT, 0, true)).toBe("2026-07-06");
    expect(columnOf(SAT, "2026-07-06", true)).toBe(0);
  });

  it("walks backwards from a weekend too", () => {
    expect(dateAtColumn(SAT, -1, true)).toBe("2026-07-03"); // the Friday before
    expect(columnOf(SAT, "2026-07-03", true)).toBe(-1);
  });

  it("a month axis over a weekend origin terminates", () => {
    // The regression this whole block exists for. It cannot fail loudly — a
    // spin has no assertion to break — so keep the inverse tests above, which
    // do.
    const axis = axisFor(SAT, 30, PPD_ANCHORS.day, undefined, "", true);
    expect(axis.coarse.length).toBeGreaterThan(0);
  });
});

describe("half-open spans", () => {
  // A range you can only fill on one side is a range you cannot use to say
  // "this starts here, the end is not settled yet" — which is exactly what a
  // milestone whose end comes from its issues needs to say.
  it("reads a start with no end as the day it starts", () => {
    expect(spanToDates("2026-07-13/")).toEqual({ start: "2026-07-13", end: "2026-07-13" });
  });
  it("reads an end with no start as the day it ends", () => {
    expect(spanToDates("/2026-07-15")).toEqual({ start: "2026-07-15", end: "2026-07-15" });
  });
  it("still rejects a range with nothing in it", () => {
    expect(spanToDates("/")).toBeNull();
    expect(spanToDates("nonsense/")).toBeNull();
  });
});

describe("barColumns", () => {
  // A span is INCLUSIVE of both ends — "2026-07-13/2026-07-15" is a three-day
  // task, not a two-day one — and the chart's own width already counts that way
  // (`columnOf(min, max) + 1`). The bar has to agree, or the end date silently
  // loses its colour and reads as "not part of the range".
  it("counts both ends: Mon→Wed is three columns", () => {
    expect(barColumns({ start: "2026-07-13", end: "2026-07-15" }, false)).toBe(3);
  });
  it("a single-day span occupies exactly one column", () => {
    expect(barColumns({ start: "2026-07-16", end: "2026-07-16" }, false)).toBe(1);
  });
  it("counts working days when weekends are skipped — Mon→Fri is five", () => {
    expect(barColumns({ start: "2026-07-20", end: "2026-07-24" }, true)).toBe(5);
  });
  it("a span that starts on a weekend still reaches its end day", () => {
    // Sat 07-11 collapses onto the next working column (Mon 07-13), so the bar
    // is the one working day it actually covers.
    expect(barColumns({ start: "2026-07-11", end: "2026-07-13" }, true)).toBe(1);
    expect(barColumns({ start: "2026-07-11", end: "2026-07-13" }, false)).toBe(3);
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

  it("only RELABELS the cross-year week — weeks after it keep the fixed jan1 numbering (no anchor shift)", () => {
    // The Jan-1 week itself is 2027 W01 (it contains Jan 1); the FOLLOWING week is
    // 2027 W02 regardless of today. by_today must not push W01 a week late.
    expect(weekNumberOf("2027-01-04", WW, "2026-06-01")).toEqual({ year: 2027, week: 2 }); // today before NY
    expect(weekNumberOf("2027-01-04", WW, "2027-06-01")).toEqual({ year: 2027, week: 2 }); // today after NY
    // The week before the overlap is 2026 W52 either way (it holds no Jan 1).
    expect(weekNumberOf("2026-12-21", WW, "2026-06-01")).toEqual({ year: 2026, week: 52 });
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

describe("weekNumberOf — a bare {} rule uses the documented defaults", () => {
  it("defaults to monday / jan1 / yearly / new_year and the {yyyy}-W{ww} label", () => {
    expect(weekNumberOf("2026-07-01", {}, "2026-08-01")).toEqual({ year: 2026, week: 27 });
    expect(weekLabelOf("2026-07-01", {}, "2026-08-01")).toBe("2026-W27");
    // new_year default: the cross-year week is the new year's W01, today-independent
    expect(weekNumberOf("2026-12-31", {}, "2020-01-01")).toEqual({ year: 2027, week: 1 });
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

  it("reset:none without an epoch counts from the Unix epoch week", () => {
    // 1970-01-01 is a Thu → its Monday week starts 1969-12-29; 1970-01-08's week
    // (1970-01-05) is one week on → W2.
    expect(weekNumberOf("1970-01-08", { start: "monday", reset: "none" }, "1970-01-08")).toEqual({ year: 1970, week: 2 });
  });

  it("when Jan 1 is itself the week start (a Monday), jan1 and first_full coincide — no cross-year straddle", () => {
    // 2024-01-01 is a Monday, so the Jan-1 week IS the first full week.
    expect(weekNumberOf("2024-01-01", { start: "monday", first_week: "jan1" }, "2024-06-01")).toEqual({ year: 2024, week: 1 });
    expect(weekNumberOf("2024-01-01", { start: "monday", first_week: "first_full" }, "2024-06-01")).toEqual({ year: 2024, week: 1 });
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

  it("with skip-weekends, a drag hops over the weekend (Fri +1 working day = Mon)", () => {
    const fri = { start: "2026-07-03", end: "2026-07-03" }; // a Friday
    expect(applyDrag(fri, "move", 1, true)).toEqual({ start: "2026-07-06", end: "2026-07-06" });
    expect(applyDrag(fri, "move", 1, false)).toEqual({ start: "2026-07-04", end: "2026-07-04" }); // calendar
  });
});
