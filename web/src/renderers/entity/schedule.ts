/**
 * The Timeline's scheduler — pure, so pressing Recalculate is a decision the
 * view merely carries out.
 *
 * The shape of it, in the user's own terms:
 *
 *   Work chains PER PERSON, because a person does one thing at a time. Within
 *   a person's queue the order is the Timeline's own order, so dragging a row
 *   is also how you reschedule. Everything nobody owns shares ONE queue — the
 *   pessimistic reading, and the honest one: unowned work does not get done in
 *   parallel by nobody.
 *
 *   A record is touched only if it says `schedule: auto`. A `manual` record is
 *   immovable AND occupies its assignee's time, so automatic work routes around
 *   it rather than through it.
 *
 *   A milestone's start is a LOWER BOUND, not a start signal: nothing in it is
 *   scheduled earlier, but a person who is still busy makes it slip. That is
 *   the choice between a late milestone and a cloned person, and a late
 *   milestone is the true one. An `auto` milestone then reaches from the
 *   earliest start to the latest end of its own issues.
 *
 * Reproducibility is the property that makes the button usable: same rows in,
 * same dates out. That is why the anchor is a milestone's start date and only
 * falls back to `today` when nothing says otherwise — and why `today` is
 * injected rather than read from the clock in here.
 */

import type { EntityInstance } from "../../api/entities";
import { dayOf, isWeekend, shiftDate, shiftWorkingDays, spanToDates, spanValue } from "./ganttScale";
import { fieldText } from "./shared";

/** Days assumed for an issue nobody has estimated. It still goes on the chart —
 * a bar you can drag into shape beats hunting for the record — but one day is
 * the smallest lie available, and the view draws it as provisional. */
const UNESTIMATED_DAYS = 1;

export type ScheduledIssue = {
  number: number;
  span: string;
  /** False when the length is the fallback, not something anyone estimated. */
  estimated: boolean;
  /** Whether this differs from what the record already holds. */
  changed: boolean;
};

export type ScheduledMilestone = { number: number; span: string; changed: boolean };

export type ScheduleReport = {
  /** Automatic issues given dates. */
  scheduled: number;
  /** Of those, ones that had been placed by hand and were pulled back onto the
   * chain — the surprise worth naming out loud. */
  movedBack: number;
  /** Manual issues, left exactly where they were. */
  untouched: number;
};

/** Which fields carry the schedule. Named by the VIEW rather than assumed, the
 * same way the week rule is: this renderer serves every app's entity types, and
 * "exp_days" is a name one schema happens to use, not a fact about timelines. */
export type ScheduleFields = {
  /** The `daterange` field being scheduled, on BOTH the record and its anchor. */
  span: string;
  /** number role — how long the work takes. */
  duration: string;
  /** `working` | `calendar`; absent, or absent on a record, means working. */
  unit?: string;
  /** `auto` | `manual` — whether this record may be moved. */
  flag: string;
  /** A `ref` field whose target's span START is this record's lower bound. */
  anchor?: string;
  /** Who does the work; records sharing a value share a queue. */
  assignee?: string;
};

export type ScheduleInput = {
  /** Issues in the order the Timeline shows them (the sorted rows). */
  issues: EntityInstance[];
  /** The records the `anchor` ref points at (and whose own spans may be auto). */
  milestones: EntityInstance[];
  /** Today, injected — the fallback anchor when no milestone says when to start. */
  today: string;
  fields: ScheduleFields;
};

export type ScheduleResult = {
  issues: ScheduledIssue[];
  milestones: ScheduledMilestone[];
  report: ScheduleReport;
};

const isAuto = (e: EntityInstance, flag: string) => (fieldText(e.fields[flag]) || "auto") !== "manual";

/** A busy stretch of one person's time: a manual issue nobody may move. */
/** One person's immovable window, as whole DAYS — this scheduler's unit. */
type Busy = { start: string; end: string };

function anchorFor(
  issue: EntityInstance,
  milestones: Map<string, EntityInstance>,
  today: string,
  anchorField: string | undefined,
  spanField: string,
): string {
  const key = anchorField ? fieldText(issue.fields[anchorField]) : "";
  const owner = key ? milestones.get(key) : undefined;
  const span = owner ? spanToDates(owner.fields[spanField]) : null;
  // The DAY, never the edge as written. A milestone starting `09:30` would
  // otherwise hand a clock to a scheduler that lays out whole days, and the
  // issue would be written with a time nobody put on it — which then feeds
  // every text comparison downstream a shape it cannot handle.
  return span ? dayOf(span.start) : today;
}

/** The first day on or after `from` that this issue may occupy: not before its
 * milestone, not while its owner is busy, and — for work counted in working
 * days — not on a weekend. */
function firstFreeDay(from: string, busy: Busy[], working: boolean): string {
  let day = from;
  for (;;) {
    if (working && isWeekend(day)) {
      day = shiftDate(day, 1);
      continue;
    }
    const clash = busy.find((b) => day >= b.start && day <= b.end);
    if (!clash) return day;
    day = shiftDate(clash.end, 1);
  }
}

export function scheduleRows({ issues, milestones, today, fields }: ScheduleInput): ScheduleResult {
  const byNumber = new Map<string, EntityInstance>();
  for (const m of milestones) byNumber.set(String(m.number), m);

  // Each person's immovable work, so automatic work can route around it. An
  // unowned manual issue blocks the unowned queue the same way.
  const busyByOwner = new Map<string, Busy[]>();
  let untouched = 0;
  for (const e of issues) {
    if (isAuto(e, fields.flag)) continue;
    untouched++;
    const span = spanToDates(e.fields[fields.span]);
    if (!span) continue;
    const owner = fields.assignee ? fieldText(e.fields[fields.assignee]) : "";
    const list = busyByOwner.get(owner) ?? [];
    // Reduced to days HERE, where the busy window is made, rather than at each
    // of the comparisons that read it. Those compare as text, and text order is
    // only chronological between two edges of the same shape: `"2026-02-02" >=
    // "2026-02-02T09:30"` is false, so a manual issue carrying a clock quietly
    // stopped blocking and the scheduler double-booked over it.
    list.push({ start: dayOf(span.start), end: dayOf(span.end) });
    busyByOwner.set(owner, list);
  }

  const cursor = new Map<string, string>();
  const out: ScheduledIssue[] = [];
  let movedBack = 0;

  for (const e of issues) {
    if (!isAuto(e, fields.flag)) continue;
    // "" is the shared unowned queue.
    const owner = fields.assignee ? fieldText(e.fields[fields.assignee]) : "";
    const raw = Number(e.fields[fields.duration]);
    const estimated = Number.isFinite(raw) && raw > 0;
    const days = estimated ? Math.floor(raw) : UNESTIMATED_DAYS;
    const working = (fields.unit ? fieldText(e.fields[fields.unit]) || "working" : "working") !== "calendar";

    const earliest = anchorFor(e, byNumber, today, fields.anchor, fields.span);
    const after = cursor.get(owner);
    const from = after && after > earliest ? after : earliest;
    const start = firstFreeDay(from, busyByOwner.get(owner) ?? [], working);
    const end = working ? shiftWorkingDays(start, days - 1, true) : shiftDate(start, days - 1);

    cursor.set(owner, shiftDate(end, 1));
    const span = spanValue({ start, end });
    const before = spanToDates(e.fields[fields.span]);
    const changed = !before || spanValue(before) !== span;
    // "Moved back" is the case worth naming: it already had dates — someone
    // dragged it — and the chain has taken it somewhere else.
    if (changed && before) movedBack++;
    out.push({ number: e.number, span, estimated, changed });
  }

  // An auto milestone reaches across its own issues, using the dates just
  // computed rather than the stale ones on the records.
  const scheduledSpan = new Map<number, string>(out.map((p) => [p.number, p.span]));
  const outMilestones: ScheduledMilestone[] = [];
  for (const m of milestones) {
    if (!isAuto(m, fields.flag)) continue;
    const mine = fields.anchor
      ? issues.filter((e) => fieldText(e.fields[fields.anchor!]) === String(m.number))
      : [];
    const spans = mine
      .map((e) => spanToDates(scheduledSpan.get(e.number) ?? e.fields[fields.span]))
      .filter((s): s is NonNullable<typeof s> => s !== null);
    if (spans.length === 0) continue;
    const start = spans.map((s) => s.start).reduce((a, b) => (b < a ? b : a));
    const end = spans.map((s) => s.end).reduce((a, b) => (b > a ? b : a));
    const span = spanValue({ start, end });
    const before = spanToDates(m.fields[fields.span]);
    outMilestones.push({ number: m.number, span, changed: !before || spanValue(before) !== span });
  }

  return { issues: out, milestones: outMilestones, report: { scheduled: out.length, movedBack, untouched } };
}
