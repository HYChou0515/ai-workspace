import { describe, expect, it } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import { backrefBuckets, backrefRecords, buildRefIndex, referencedTypes, refOptions, traverseColumn } from "./refTraversal";

const issueType: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text" },
    { name: "milestone", role: "ref", to: "milestone" },
  ],
  form: [],
};

const rec = (number: number, fields: Record<string, unknown>): EntityInstance => ({
  number,
  type_name: "issue",
  fields,
  body: "",
  diagnostics: [],
});
const ms = (number: number, fields: Record<string, unknown>): EntityInstance => ({
  number,
  type_name: "milestone",
  fields,
  body: "",
  diagnostics: [],
});

const milestoneType: EntityType = {
  name: "milestone",
  records_path: "milestones",
  fields: [
    { name: "title", role: "text" },
    { name: "span", role: "daterange" },
    { name: "issues", role: "backref", from: "issue.milestone" },
  ],
  form: [],
};

describe("referencedTypes", () => {
  it("lists the target types of the schema's ref fields", () => {
    expect(referencedTypes(issueType)).toEqual(["milestone"]);
  });
  it("is empty for a schema with no refs (and for a null type)", () => {
    expect(referencedTypes({ ...issueType, fields: [{ name: "title", role: "text" }] })).toEqual([]);
    expect(referencedTypes(null)).toEqual([]);
  });
  it("also lists the types that point BACK at this one (#785)", () => {
    // A view over milestones has to load issues to know what a milestone
    // reaches over. Without this the corpus is never fetched and the feature
    // is silently a no-op — there is nothing to union.
    expect(referencedTypes(milestoneType)).toEqual(["issue"]);
  });
});

describe("backrefRecords (#785)", () => {
  const index = buildRefIndex({
    issue: [
      rec(1, { title: "mine", milestone: 1 }),
      rec(2, { title: "someone else's", milestone: 2 }),
      rec(3, { title: "unassigned" }),
      rec(4, { title: "mine too", milestone: "1" }),
    ],
  });

  it("finds the records whose ref points at this one", () => {
    const found = backrefRecords(ms(1, { title: "M1" }), milestoneType, index);
    // Written as a string by the form, as a number by the projection — both are
    // the same milestone to a reader, so both have to be to this.
    expect(found.map((r) => r.number)).toEqual([1, 4]);
  });

  it("is empty for a type with no backref, and when nothing points here", () => {
    expect(backrefRecords(ms(1, {}), issueType, index)).toEqual([]);
    expect(backrefRecords(ms(9, {}), milestoneType, index)).toEqual([]);
    expect(backrefRecords(ms(1, {}), milestoneType, new Map())).toEqual([]);
  });

  it("buckets the whole corpus in one pass, agreeing with the per-record answer", () => {
    // A roadmap is milestones × issues. Asking each milestone to filter every
    // issue is quadratic in the two numbers that both grow with the project, and
    // it happens inside a render — so the grouping is done once and read by
    // number. Same answers, or the cheap path is a different feature.
    const buckets = backrefBuckets(milestoneType, index);
    for (const n of [1, 2, 9]) {
      expect(buckets.get(n) ?? []).toEqual(backrefRecords(ms(n, {}), milestoneType, index));
    }
    // Records pointing nowhere are in nobody's bucket.
    expect([...buckets.values()].flat().map((r) => r.number)).not.toContain(3);
  });

  it("buckets nothing for a type with no backref, or an empty corpus", () => {
    expect(backrefBuckets(issueType, index).size).toBe(0);
    expect(backrefBuckets(milestoneType, new Map()).size).toBe(0);
    expect(backrefBuckets(null, index).size).toBe(0);
  });
});

describe("traverseColumn (ref-path resolution is the renderer's job, §A4)", () => {
  const index = buildRefIndex({ milestone: [ms(5, { title: "v1.0" })] });

  it("follows a ref number into the target type and reads the sub-field", () => {
    expect(traverseColumn("milestone.title", rec(1, { milestone: 5 }), issueType, index)).toEqual({
      text: "v1.0",
      dangling: false,
    });
  });
  it("marks a dangling ref when the target record is missing", () => {
    expect(traverseColumn("milestone.title", rec(1, { milestone: 9 }), issueType, index)).toEqual({
      text: "#9?",
      dangling: true,
    });
  });
  it("returns empty (not dangling) when the ref is unset", () => {
    expect(traverseColumn("milestone.title", rec(1, {}), issueType, index)).toEqual({ text: "", dangling: false });
  });
  it("returns null for a plain (non-dotted) column", () => {
    expect(traverseColumn("title", rec(1, { title: "A" }), issueType, index)).toBeNull();
  });
  it("returns null when the dotted base field isn't a ref", () => {
    expect(traverseColumn("title.x", rec(1, {}), issueType, index)).toBeNull();
  });
});

describe("refOptions (the #N-title picker options)", () => {
  it("lists target records as {number,label}, falling back to #N without a title", () => {
    const index = buildRefIndex({ milestone: [ms(5, { title: "v1.0" }), ms(6, {})] });
    expect(refOptions(issueType.fields[1], index)).toEqual([
      { number: 5, label: "v1.0" },
      { number: 6, label: "#6" },
    ]);
  });
  it("is empty when the ref target type has no records loaded", () => {
    expect(refOptions(issueType.fields[1], buildRefIndex({}))).toEqual([]);
  });
});
