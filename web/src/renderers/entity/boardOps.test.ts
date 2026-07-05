import { describe, expect, it, vi } from "vitest";

import type { EntityFieldSpec, EntityInstance } from "../../api/entities";
import { dropPatch, handleDragEnd, partitionColumns, UNSET_COL } from "./boardOps";

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
