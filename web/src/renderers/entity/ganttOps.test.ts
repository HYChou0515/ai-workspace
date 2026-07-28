import { describe, expect, it } from "vitest";

import type { EntityInstance } from "../../api/entities";
import { rowDropResult } from "./ganttOps";

const rec = (number: number, fields: Record<string, unknown>): EntityInstance => ({
  number,
  type_name: "issue",
  fields,
  body: "",
  diagnostics: [],
});

// Two milestones' worth of issues, in the order the Timeline shows them.
const rows = [
  rec(1, { title: "A", milestone: 1, rank: 1 }),
  rec(2, { title: "B", milestone: 1, rank: 2 }),
  rec(3, { title: "C", milestone: 2, rank: 3 }),
  rec(4, { title: "D", milestone: 2, rank: 4 }),
  rec(5, { title: "E", rank: 5 }), // no milestone — the (ungrouped) lane
];

describe("rowDropResult", () => {
  it("reorders within a lane by writing rank alone — the group is untouched", () => {
    const out = rowDropResult(rows, "milestone", 2, 1);
    expect(out).toEqual({ number: 2, patch: { rank: 0 } });
  });

  it("carries the record into the lane it was dropped on", () => {
    // B (milestone 1) dropped onto D (milestone 2): it belongs to milestone 2
    // now, ranked into that lane — dragging across a swimlane used to be a
    // silent no-op, so the drop looked broken.
    const out = rowDropResult(rows, "milestone", 2, 4);
    expect(out?.number).toBe(2);
    expect(out?.patch.milestone).toBe(2);
    expect(out?.patch.rank).toBe(3.5); // between C (3) and D (4)
  });

  it("keeps the group value's own type — a ref stays a number, not its label", () => {
    const out = rowDropResult(rows, "milestone", 5, 3);
    expect(out?.patch.milestone).toBe(2);
    expect(typeof out?.patch.milestone).toBe("number");
  });

  it("clears the field when the drop lands in the (ungrouped) lane", () => {
    const out = rowDropResult(rows, "milestone", 1, 5);
    expect(out?.number).toBe(1);
    expect(out?.patch).toHaveProperty("milestone", null);
  });

  it("ranks to the top when dropped on the first row of a lane", () => {
    const out = rowDropResult(rows, "milestone", 4, 3);
    expect(out?.patch.rank).toBe(2); // above C (rank 3)
  });

  it("is a no-op on itself, or on a row that isn't charted", () => {
    expect(rowDropResult(rows, "milestone", 2, 2)).toBeNull();
    expect(rowDropResult(rows, "milestone", 2, 99)).toBeNull();
  });

  it("with no group_by there is one lane, so only rank moves", () => {
    const out = rowDropResult(rows, undefined, 4, 1);
    expect(out).toEqual({ number: 4, patch: { rank: 0 } });
  });

  it("groups by a plain string field the same way", () => {
    const byStatus = [
      rec(1, { title: "A", status: "open", rank: 1 }),
      rec(2, { title: "B", status: "done", rank: 2 }),
    ];
    const out = rowDropResult(byStatus, "status", 1, 2);
    expect(out?.patch.status).toBe("done");
  });
});
