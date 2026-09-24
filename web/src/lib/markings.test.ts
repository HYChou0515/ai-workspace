/**
 * Named markings (#847 PR 3 P1, Q5.1 / Q6): a knowledge-free `name → {column →
 * values}` store. Columns and values are opaque strings; views link on
 * same-named columns.
 */
import { describe, expect, it, vi } from "vitest";

import { isLit, MarkingStore, projectOntoKeys } from "./markings";

describe("isLit — the matching rule", () => {
  const marking = { lot: new Set(["L1", "L2"]), wafer: new Set(["3"]) };

  it("lights a row whose every shared column is in the marking", () => {
    expect(isLit({ lot: "L1", wafer: "3", x: "0" }, marking)).toBe(true);
    // Only `lot` is shared: `wafer` is not this view's column, so it does not
    // constrain it — a lot-level table lights every row of a marked lot.
    expect(isLit({ lot: "L2", yield: "0.9" }, marking)).toBe(true);
  });

  it("does not light a row that misses on any shared column", () => {
    expect(isLit({ lot: "L1", wafer: "4" }, marking)).toBe(false);
    expect(isLit({ lot: "L9" }, marking)).toBe(false);
  });

  it("does not light a row that shares NO column — an unrelated view stays dark", () => {
    // "every shared column matches" is vacuously true with none shared; taken
    // literally it would light every row of a view keyed on something else.
    expect(isLit({ tool: "ETCH-1" }, marking)).toBe(false);
  });

  it("lights nothing for an empty marking", () => {
    expect(isLit({ lot: "L1" }, {})).toBe(false);
  });

  it("compares values as strings — opaque, no parsing", () => {
    expect(isLit({ wafer: "3" }, { wafer: new Set(["3"]) })).toBe(true);
    expect(isLit({ wafer: "03" }, { wafer: new Set(["3"]) })).toBe(false);
  });
});

describe("projectOntoKeys — what a selection writes", () => {
  it("collects each key's distinct values over the selected rows", () => {
    const rows = [
      { lot: "L1", wafer: "3", x: "0" },
      { lot: "L1", wafer: "4", x: "1" },
      { lot: "L2", wafer: "3", x: "2" },
    ];
    const m = projectOntoKeys(rows, ["lot", "wafer"])!;
    expect([...m.lot!].sort()).toEqual(["L1", "L2"]);
    expect([...m.wafer!].sort()).toEqual(["3", "4"]);
    expect(Object.keys(m)).toEqual(["lot", "wafer"]);
  });

  it("is null for a view without keys — it can be lit but cannot write", () => {
    expect(projectOntoKeys([{ lot: "L1" }], [])).toBeNull();
  });

  it("is an empty marking for an empty selection (the write that clears)", () => {
    expect(projectOntoKeys([], ["lot"])).toEqual({});
  });

  it("skips a key a row does not carry", () => {
    expect(projectOntoKeys([{ lot: "L1" }], ["lot", "wafer"])).toEqual({ lot: new Set(["L1"]) });
  });
});

describe("MarkingStore", () => {
  it("reads back what was written, with the view that wrote it", () => {
    const s = new MarkingStore();
    s.set("fail", { lot: new Set(["L1"]) }, "/v/grid.ai.yaml");
    expect(s.get("fail")?.marking).toEqual({ lot: new Set(["L1"]) });
    expect(s.get("fail")?.source).toBe("/v/grid.ai.yaml");
    expect(s.get("other")).toBeUndefined();
  });

  it("notifies only the subscribers of the marking that changed", () => {
    const s = new MarkingStore();
    const onFail = vi.fn();
    const onOther = vi.fn();
    s.subscribe("fail", onFail);
    s.subscribe("other", onOther);
    s.set("fail", { lot: new Set(["L1"]) }, null);
    expect(onFail).toHaveBeenCalledTimes(1);
    expect(onOther).not.toHaveBeenCalled();
  });

  it("an empty marking clears the name", () => {
    const s = new MarkingStore();
    s.set("fail", { lot: new Set(["L1"]) }, null);
    s.set("fail", {}, null);
    expect(s.get("fail")).toBeUndefined();
    s.set("fail", { lot: new Set() }, null);
    expect(s.get("fail")).toBeUndefined();
  });

  it("lists the names that hold a marking, for the send chips", () => {
    const s = new MarkingStore();
    const onNames = vi.fn();
    s.subscribeNames(onNames);
    s.set("b", { lot: new Set(["L1"]) }, null);
    s.set("a", { lot: new Set(["L2"]) }, null);
    expect(s.names()).toEqual(["a", "b"]);
    s.set("b", null, null);
    expect(s.names()).toEqual(["a"]);
    expect(onNames).toHaveBeenCalledTimes(3);
  });

  it("an unsubscribed listener hears nothing more", () => {
    const s = new MarkingStore();
    const cb = vi.fn();
    const off = s.subscribe("fail", cb);
    off();
    s.set("fail", { lot: new Set(["L1"]) }, null);
    expect(cb).not.toHaveBeenCalled();
  });

  it("returns the same snapshot until the marking changes (useSyncExternalStore)", () => {
    const s = new MarkingStore();
    s.set("fail", { lot: new Set(["L1"]) }, null);
    expect(s.get("fail")).toBe(s.get("fail"));
    expect(s.names()).toBe(s.names());
  });

  it("offers one snapshot of every marking, new only after a write (the composer's chips)", () => {
    const s = new MarkingStore();
    const onAny = vi.fn();
    s.subscribeAll(onAny);
    const empty = s.snapshot();
    expect(s.snapshot()).toBe(empty);
    s.set("fail", { lot: new Set(["L1"]) }, "/v/a.ai.yaml");
    s.set("fail", { lot: new Set(["L1", "L2"]) }, "/v/a.ai.yaml");
    expect(onAny).toHaveBeenCalledTimes(2);
    const snap = s.snapshot();
    expect(snap).not.toBe(empty);
    expect([...snap.keys()]).toEqual(["fail"]);
    expect(snap.get("fail")!.marking.lot!.size).toBe(2);
  });

  it("does not keep the caller's sets — a later mutation cannot change the marking", () => {
    const s = new MarkingStore();
    const lots = new Set(["L1"]);
    s.set("fail", { lot: lots }, null);
    lots.add("L2");
    expect([...s.get("fail")!.marking.lot!]).toEqual(["L1"]);
  });
});
