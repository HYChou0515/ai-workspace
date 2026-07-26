import { describe, expect, it } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import { buildRefIndex } from "./refTraversal";
import { sortRows } from "./sortRows";
import type { SortRule } from "./types";

const type: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text" },
    { name: "status", role: "status", values: ["open", "in_progress", "blocked", "done"] },
    { name: "assignee", role: "actor" },
    { name: "due", role: "date" },
    { name: "progress", role: "progress" },
    { name: "milestone", role: "ref", to: "milestone" },
    { name: "rank", role: "rank" },
  ],
  form: [],
};
const users: User[] = [
  { id: "alice", name: "Alice Chen", section: "", email: "", photo_url: null },
  { id: "bob", name: "Bob Liu", section: "", email: "", photo_url: null },
];
const refIndex = buildRefIndex({
  milestone: [
    { number: 10, type_name: "milestone", fields: { title: "v1.0" }, body: "", diagnostics: [] },
    { number: 11, type_name: "milestone", fields: { title: "v2.0" }, body: "", diagnostics: [] },
  ],
});
const rec = (number: number, fields: Record<string, unknown>): EntityInstance => ({
  number,
  type_name: "issue",
  fields,
  body: "",
  diagnostics: [],
});
const nums = (rows: EntityInstance[]) => rows.map((r) => r.number);
const asc = (field: string): SortRule[] => [{ field, dir: "asc" }];
const desc = (field: string): SortRule[] => [{ field, dir: "desc" }];

describe("sortRows", () => {
  it("with no sort, orders by the manual rank (drag position)", () => {
    const rows = [rec(1, { rank: 3 }), rec(2, { rank: 1 }), rec(3, { rank: 2 })];
    expect(nums(sortRows(rows, undefined, type, refIndex, users))).toEqual([2, 3, 1]);
  });

  it("with no sort, a record with no rank falls back to its number", () => {
    const rows = [rec(5, {}), rec(2, { rank: 1.5 }), rec(1, { rank: 1 })];
    // ranks 1 (#1), 1.5 (#2), then un-ranked #5 by its number (5)
    expect(nums(sortRows(rows, undefined, type, refIndex, users))).toEqual([1, 2, 5]);
  });

  it("orders a status by its declared vocabulary, not alphabetically", () => {
    const rows = [rec(1, { status: "done" }), rec(2, { status: "open" }), rec(3, { status: "blocked" })];
    // vocab order open < in_progress < blocked < done — NOT alpha (blocked<done<open)
    expect(nums(sortRows(rows, asc("status"), type, refIndex, users))).toEqual([2, 3, 1]);
  });

  it("reverses on desc", () => {
    const rows = [rec(1, { status: "open" }), rec(2, { status: "done" })];
    expect(nums(sortRows(rows, desc("status"), type, refIndex, users))).toEqual([2, 1]);
  });

  it("breaks ties with the next tier (multi-level)", () => {
    const rows = [
      rec(1, { status: "open", title: "Beta" }),
      rec(2, { status: "open", title: "Alpha" }),
      rec(3, { status: "done", title: "Zeta" }),
    ];
    const sort: SortRule[] = [
      { field: "status", dir: "asc" },
      { field: "title", dir: "asc" },
    ];
    expect(nums(sortRows(rows, sort, type, refIndex, users))).toEqual([2, 1, 3]);
  });

  it("orders a ref by the referenced title, an actor by the person's name", () => {
    const byMilestone = [rec(1, { milestone: 11 }), rec(2, { milestone: 10 })];
    expect(nums(sortRows(byMilestone, asc("milestone"), type, refIndex, users))).toEqual([2, 1]); // v1.0 < v2.0
    const byAssignee = [rec(1, { assignee: "bob" }), rec(2, { assignee: "alice" })];
    expect(nums(sortRows(byAssignee, asc("assignee"), type, refIndex, users))).toEqual([2, 1]); // Alice < Bob
  });

  it("orders numbers and dates naturally", () => {
    const byProgress = [rec(1, { progress: 80 }), rec(2, { progress: 10 })];
    expect(nums(sortRows(byProgress, asc("progress"), type, refIndex, users))).toEqual([2, 1]);
    const byDue = [rec(1, { due: "2026-08-01" }), rec(2, { due: "2026-07-01" })];
    expect(nums(sortRows(byDue, asc("due"), type, refIndex, users))).toEqual([2, 1]);
  });

  it("sorts missing values last in either direction", () => {
    const rows = [rec(1, { status: "open" }), rec(2, {}), rec(3, { status: "done" })];
    expect(nums(sortRows(rows, asc("status"), type, refIndex, users))).toEqual([1, 3, 2]);
    expect(nums(sortRows(rows, desc("status"), type, refIndex, users))).toEqual([3, 1, 2]);
  });

  it("final tie-break is the manual rank when the sorted field ties", () => {
    const rows = [
      rec(3, { status: "open", rank: 2 }),
      rec(1, { status: "open", rank: 3 }),
      rec(2, { status: "open", rank: 1 }),
    ];
    // all 'open' → fall through to rank asc: #2(1), #3(2), #1(3)
    expect(nums(sortRows(rows, asc("status"), type, refIndex, users))).toEqual([2, 3, 1]);
  });

  it("does not mutate the input array", () => {
    const rows = [rec(2, { rank: 2 }), rec(1, { rank: 1 })];
    sortRows(rows, undefined, type, refIndex, users);
    expect(nums(rows)).toEqual([2, 1]);
  });
});
