import { describe, expect, it, vi } from "vitest";

import type { EntityFieldSpec, EntityInstance } from "../../api/entities";
import { dropPatch, dropResult, handleDragEnd, partitionColumns, UNSET_COL } from "./boardOps";

const rec = (number: number, fields: Record<string, unknown>): EntityInstance => ({
  number,
  type_name: "issue",
  fields,
  body: "",
  diagnostics: [],
});
const statusSpec: EntityFieldSpec = { name: "status", role: "status", values: ["open", "done"] };

describe("partitionColumns", () => {
  it("splits the closed vocab from out-of-vocab values present in the data (§A3/§D)", () => {
    const es = [rec(1, { status: "open" }), rec(2, { status: "weird" }), rec(3, { status: "done" })];
    expect(partitionColumns(statusSpec, es, "status")).toEqual({ known: ["open", "done"], extra: ["weird"] });
  });

  it("treats every present value as a column when the field has no closed vocab", () => {
    const es = [rec(1, { status: "a" }), rec(2, { status: "b" })];
    expect(partitionColumns({ name: "status", role: "status" }, es, "status")).toEqual({ known: ["a", "b"], extra: [] });
  });
});

describe("dropPatch", () => {
  it("moves a card to a known column", () => {
    expect(dropPatch("card-3", "col-done", "status")).toEqual({ number: 3, patch: { status: "done" } });
  });
  it("clears the status when dropped on the unset column", () => {
    expect(dropPatch("card-3", `col-${UNSET_COL}`, "status")).toEqual({ number: 3, patch: { status: null } });
  });
  it("is a no-op when dropped outside any column", () => {
    expect(dropPatch("card-3", null, "status")).toBeNull();
  });
});

describe("handleDragEnd", () => {
  it("patches the card's status from a drag onto a column", () => {
    const onPatch = vi.fn();
    handleDragEnd({ active: { id: "card-3" }, over: { id: "col-done" } }, "status", onPatch);
    expect(onPatch).toHaveBeenCalledWith(3, { status: "done" });
  });
  it("does nothing when the card is dropped nowhere", () => {
    const onPatch = vi.fn();
    handleDragEnd({ active: { id: "card-3" }, over: null }, "status", onPatch);
    expect(onPatch).not.toHaveBeenCalled();
  });
});

describe("dropResult (card-onto-card reorder, #GH-projects P4)", () => {
  const entities = [
    rec(1, { status: "open", rank: 10 }),
    rec(2, { status: "open", rank: 20 }),
    rec(3, { status: "done", rank: 30 }),
  ];

  it("reorders within a column: adopt status + a rank in front of the target", () => {
    // drop #1 in front of #2 (open column) → between nothing-above and #2(20) = 19
    expect(dropResult("card-1", "card-2", "status", entities, false)).toEqual({
      number: 1,
      patch: { status: "open", rank: 19 },
    });
  });

  it("cross-column card drop adopts the target's status (and a rank there)", () => {
    // drop #1 onto #3 (done column: just #3(30)) → status done, rank 30 - 1 = 29
    expect(dropResult("card-1", "card-3", "status", entities, false)).toEqual({
      number: 1,
      patch: { status: "done", rank: 29 },
    });
  });

  it("with a sort active, a card drop changes status but writes NO rank (GitHub model)", () => {
    expect(dropResult("card-1", "card-3", "status", entities, true)).toEqual({
      number: 1,
      patch: { status: "done" },
    });
  });

  it("a column drop still just sets the status; a self-drop is a no-op", () => {
    expect(dropResult("card-1", "col-done", "status", entities, false)).toEqual({ number: 1, patch: { status: "done" } });
    expect(dropResult("card-1", "card-1", "status", entities, false)).toBeNull();
  });
});
