import { describe, expect, it } from "vitest";

import type { EntityInstance } from "../../api/entities";
import { scheduleRows } from "./schedule";

// The PM app's names for the scheduling fields; the view file declares them.
const FIELDS = {
  span: "span",
  duration: "exp_days",
  unit: "exp_days_unit",
  flag: "schedule",
  anchor: "milestone",
  assignee: "assignee",
};

const issue = (number: number, fields: Record<string, unknown>): EntityInstance => ({
  number,
  type_name: "issue",
  fields: { schedule: "auto", ...fields },
  body: "",
  diagnostics: [],
});
const milestone = (number: number, fields: Record<string, unknown>): EntityInstance => ({
  number,
  type_name: "milestone",
  fields,
  body: "",
  diagnostics: [],
});

/** The scheduler takes the rows in the order the Timeline shows them. */
const run = (issues: EntityInstance[], milestones: EntityInstance[] = [], today = "2026-07-01") =>
  scheduleRows({ issues, milestones, today, fields: FIELDS });

const spanOf = (out: ReturnType<typeof run>, number: number) =>
  out.issues.find((p) => p.number === number)?.span;

describe("a span that names a clock is still a DAY to the scheduler (#785)", () => {
  // `spanToDates` can return `YYYY-MM-DDTHH:mm` since #785, and every
  // comparison in this module is text over bare dates. `"2026-02-02" >=
  // "2026-02-02T09:30"` is false — the shorter string sorts first — so a manual
  // issue that happens to carry a clock stops blocking, and the scheduler
  // books somebody onto work they are already doing.
  it("routes automatic work around a manual span that carries a clock", () => {
    const plain = run(
      [
        issue(1, { assignee: "alice", schedule: "manual", span: "2026-07-02/2026-07-03" }),
        issue(2, { assignee: "alice", exp_days: 1 }),
      ],
      [],
      "2026-07-02",
    );
    const timed = run(
      [
        issue(1, {
          assignee: "alice",
          schedule: "manual",
          span: "2026-07-02T09:30/2026-07-03T17:00",
        }),
        issue(2, { assignee: "alice", exp_days: 1 }),
      ],
      [],
      "2026-07-02",
    );
    // Same two days blocked, so the same answer — the clock says WHEN in the
    // day, and this scheduler lays out whole days.
    expect(spanOf(timed, 2)).toBe(spanOf(plain, 2));
    expect(spanOf(timed, 2)).toBe("2026-07-06/2026-07-06");
  });

  it("anchors to the milestone's DAY, never writing a clock the issue never had", () => {
    const out = run(
      [issue(1, { assignee: "alice", exp_days: 1, milestone: 1 })],
      [milestone(1, { span: "2026-07-09T09:30/" })],
    );
    expect(spanOf(out, 1)).toBe("2026-07-09/2026-07-09");
  });
});

describe("scheduleRows — one queue per assignee", () => {
  it("chains a person's work: the next task starts after the last one ends", () => {
    const out = run(
      [
        issue(1, { assignee: "alice", exp_days: 3, milestone: 1 }),
        issue(2, { assignee: "alice", exp_days: 2, milestone: 1 }),
      ],
      [milestone(1, { span: "2026-07-01/" })],
    );
    // 2026-07-01 is a Wednesday; working days by default.
    expect(spanOf(out, 1)).toBe("2026-07-01/2026-07-03");
    expect(spanOf(out, 2)).toBe("2026-07-06/2026-07-07"); // Mon+Tue, weekend skipped
  });

  it("gives each person their own queue — two people work at the same time", () => {
    const out = run(
      [
        issue(1, { assignee: "alice", exp_days: 3, milestone: 1 }),
        issue(2, { assignee: "bob", exp_days: 3, milestone: 1 }),
      ],
      [milestone(1, { span: "2026-07-01/" })],
    );
    expect(spanOf(out, 1)).toBe("2026-07-01/2026-07-03");
    expect(spanOf(out, 2)).toBe("2026-07-01/2026-07-03");
  });

  it("puts everything nobody owns in ONE queue — the pessimistic reading", () => {
    const out = run(
      [issue(1, { exp_days: 3, milestone: 1 }), issue(2, { exp_days: 2, milestone: 1 })],
      [milestone(1, { span: "2026-07-01/" })],
    );
    expect(spanOf(out, 1)).toBe("2026-07-01/2026-07-03");
    expect(spanOf(out, 2)).toBe("2026-07-06/2026-07-07");
  });

  it("follows the order it was given, not the issue numbers", () => {
    const out = run(
      [
        issue(2, { assignee: "alice", exp_days: 2, milestone: 1 }),
        issue(1, { assignee: "alice", exp_days: 3, milestone: 1 }),
      ],
      [milestone(1, { span: "2026-07-01/" })],
    );
    expect(spanOf(out, 2)).toBe("2026-07-01/2026-07-02");
    expect(spanOf(out, 1)).toBe("2026-07-03/2026-07-07");
  });
});

describe("scheduleRows — what it may and may not touch", () => {
  it("never moves a manual issue, and routes automatic work around it", () => {
    const out = run(
      [
        issue(1, { assignee: "alice", schedule: "manual", span: "2026-07-01/2026-07-03" }),
        issue(2, { assignee: "alice", exp_days: 2, milestone: 1 }),
      ],
      [milestone(1, { span: "2026-07-01/" })],
    );
    expect(spanOf(out, 1)).toBeUndefined(); // untouched — not in the write set
    expect(spanOf(out, 2)).toBe("2026-07-06/2026-07-07"); // after Alice's fixed work
  });

  it("a manual issue holds its time even when it sits later in the order", () => {
    const out = run(
      [
        issue(2, { assignee: "alice", exp_days: 3, milestone: 1 }),
        issue(1, { assignee: "alice", schedule: "manual", span: "2026-07-01/2026-07-02" }),
      ],
      [milestone(1, { span: "2026-07-01/" })],
    );
    // The auto issue cannot start on the 1st — Alice is busy until the 2nd.
    expect(spanOf(out, 2)).toBe("2026-07-03/2026-07-07");
  });
});

describe("scheduleRows — milestones", () => {
  it("never starts a milestone's work before the milestone does", () => {
    const out = run(
      [issue(1, { assignee: "alice", exp_days: 2, milestone: 2 })],
      [milestone(2, { span: "2026-08-03/" })],
    );
    expect(spanOf(out, 1)).toBe("2026-08-03/2026-08-04");
  });

  it("lets a milestone slip rather than clone the person", () => {
    // Alice's M1 work runs to 2026-08-11, past M2's own start of 2026-08-01.
    const out = run(
      [
        issue(1, { assignee: "alice", exp_days: 30, milestone: 1 }),
        issue(2, { assignee: "alice", exp_days: 3, milestone: 2 }),
      ],
      [milestone(1, { span: "2026-07-01/" }), milestone(2, { span: "2026-08-01/" })],
    );
    expect(spanOf(out, 1)).toBe("2026-07-01/2026-08-11");
    expect(spanOf(out, 2)).toBe("2026-08-12/2026-08-14"); // waits for her, not 08-03
  });

  it("gives an auto milestone the reach of its issues", () => {
    const out = run(
      [
        issue(1, { assignee: "alice", exp_days: 3, milestone: 1 }),
        issue(2, { assignee: "bob", exp_days: 8, milestone: 1 }),
      ],
      [milestone(1, { span: "2026-07-01/", schedule: "auto" })],
    );
    expect(out.milestones.find((m) => m.number === 1)?.span).toBe("2026-07-01/2026-07-10");
  });

  it("leaves a manual milestone's own dates alone", () => {
    const out = run(
      [issue(1, { assignee: "alice", exp_days: 3, milestone: 1 })],
      [milestone(1, { span: "2026-07-01/2026-12-31", schedule: "manual" })],
    );
    expect(out.milestones).toHaveLength(0);
  });

  it("falls back to today when nothing says when to start", () => {
    const out = run([issue(1, { assignee: "alice", exp_days: 2 })], [], "2026-09-07");
    expect(spanOf(out, 1)).toBe("2026-09-07/2026-09-08");
  });
});

describe("scheduleRows — durations", () => {
  it("counts calendar days when the issue says so", () => {
    const out = run(
      [issue(1, { assignee: "alice", exp_days: 3, exp_days_unit: "calendar", milestone: 1 })],
      [milestone(1, { span: "2026-07-03/" })],
    );
    expect(spanOf(out, 1)).toBe("2026-07-03/2026-07-05"); // Fri→Sun, weekend included
  });

  it("starts working-day work on a working day", () => {
    const out = run(
      [issue(1, { assignee: "alice", exp_days: 2, milestone: 1 })],
      [milestone(1, { span: "2026-07-04/" })], // a Saturday
    );
    expect(spanOf(out, 1)).toBe("2026-07-06/2026-07-07");
  });

  it("gives an unestimated issue a day, so it lands on the chart to be dragged", () => {
    const out = run(
      [issue(1, { assignee: "alice", milestone: 1 })],
      [milestone(1, { span: "2026-07-01/" })],
    );
    expect(spanOf(out, 1)).toBe("2026-07-01/2026-07-01");
    expect(out.issues.find((p) => p.number === 1)?.estimated).toBe(false);
  });
});

describe("scheduleRows — what it reports", () => {
  it("counts what it placed and what it put back", () => {
    const out = run(
      [
        issue(1, { assignee: "alice", exp_days: 2, span: "2026-01-01/2026-01-02", milestone: 1 }),
        issue(2, { assignee: "alice", exp_days: 2, milestone: 1 }),
        issue(3, { assignee: "alice", schedule: "manual", span: "2026-07-01/2026-07-01" }),
      ],
      [milestone(1, { span: "2026-07-01/" })],
    );
    // #1 had been dragged somewhere by hand; it is auto, so it comes back.
    expect(out.report.scheduled).toBe(2);
    expect(out.report.movedBack).toBe(1);
    expect(out.report.untouched).toBe(1);
  });

  it("writes nothing for a record already sitting where it belongs", () => {
    const first = run(
      [issue(1, { assignee: "alice", exp_days: 2, milestone: 1 })],
      [milestone(1, { span: "2026-07-01/" })],
    );
    const settled = issue(1, {
      assignee: "alice",
      exp_days: 2,
      milestone: 1,
      span: spanOf(first, 1),
    });
    const again = run([settled], [milestone(1, { span: "2026-07-01/" })]);
    // Idempotent: pressing the button twice writes nothing the second time.
    expect(again.issues.filter((p) => p.changed)).toHaveLength(0);
  });
});
