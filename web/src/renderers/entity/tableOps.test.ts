import { describe, expect, it } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import { buildRefIndex } from "./refTraversal";
import { filterEntities, sortEntities } from "./tableOps";

const type: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text" },
    { name: "status", role: "status", values: ["open", "done"] },
    { name: "progress", role: "progress" },
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
const nums = (es: EntityInstance[]) => es.map((e) => e.number);

describe("sortEntities", () => {
  const es = [rec(1, { title: "Beta" }), rec(2, { title: "alpha" }), rec(3, { title: "Gamma" })];

  it("sorts text ascending / descending, stable by number", () => {
    expect(nums(sortEntities(es, "title", "asc", type, undefined))).toEqual([2, 1, 3]);
    expect(nums(sortEntities(es, "title", "desc", type, undefined))).toEqual([3, 1, 2]);
  });

  it("sorts a numeric role numerically (not lexicographically)", () => {
    const ns = [rec(1, { progress: 9 }), rec(2, { progress: 100 }), rec(3, { progress: 20 })];
    expect(nums(sortEntities(ns, "progress", "asc", type, undefined))).toEqual([1, 3, 2]);
  });

  it("sorts a ref-traversal column by the resolved target text", () => {
    const index = buildRefIndex({
      milestone: [
        { number: 5, type_name: "milestone", fields: { title: "v2" }, body: "", diagnostics: [] },
        { number: 6, type_name: "milestone", fields: { title: "v1" }, body: "", diagnostics: [] },
      ],
    });
    const es2 = [rec(1, { milestone: 5 }), rec(2, { milestone: 6 })];
    expect(nums(sortEntities(es2, "milestone.title", "asc", type, index))).toEqual([2, 1]);
  });
});

describe("filterEntities", () => {
  const es = [rec(1, { status: "open" }), rec(2, { status: "done" }), rec(3, { status: "open" })];

  it("keeps only rows matching an active value filter", () => {
    expect(nums(filterEntities(es, { status: "open" }, type, undefined))).toEqual([1, 3]);
  });
  it("returns all rows when no filter is active (empty value = all)", () => {
    expect(nums(filterEntities(es, { status: "" }, type, undefined))).toEqual([1, 2, 3]);
  });
  it("ANDs multiple active filters", () => {
    const es2 = [rec(1, { status: "open", title: "a" }), rec(2, { status: "open", title: "b" })];
    expect(nums(filterEntities(es2, { status: "open", title: "b" }, type, undefined))).toEqual([2]);
  });
});
