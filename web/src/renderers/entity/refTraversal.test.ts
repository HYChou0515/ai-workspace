import { describe, expect, it } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import { buildRefIndex, referencedTypes, refOptions, traverseColumn } from "./refTraversal";

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

describe("referencedTypes", () => {
  it("lists the target types of the schema's ref fields", () => {
    expect(referencedTypes(issueType)).toEqual(["milestone"]);
  });
  it("is empty for a schema with no refs (and for a null type)", () => {
    expect(referencedTypes({ ...issueType, fields: [{ name: "title", role: "text" }] })).toEqual([]);
    expect(referencedTypes(null)).toEqual([]);
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
