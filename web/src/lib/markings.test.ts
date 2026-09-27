/**
 * Named markings (#847 PR 3 P1, Q5.1 / Q6): a knowledge-free `name → {column →
 * values}` store. Columns and values are opaque strings; views link on
 * same-named columns.
 */
import { describe, expect, it, vi } from "vitest";

import { isLit, markedBy, markingFrom, markingRows, markingSize, MarkingStore, projectOntoKeys } from "./markings";

const one = (key: string, ...values: string[]) => markingFrom([key], values.map((v) => [v]));

describe("isLit — a marking remembers the picked rows (#861 D1, D2)", () => {
  // Four gallery tiles boxed: (g3, 8), (g4, 1), (g5, 1), (g5, 2).
  const marking = markingFrom(["group", "item"], [["g3", "8"], ["g4", "1"], ["g5", "1"], ["g5", "2"]]);

  it("lights exactly the picked rows, not every combination of their values (D1)", () => {
    expect(isLit({ group: "g3", item: "8", x: "0" }, marking)).toBe(true);
    expect(isLit({ group: "g5", item: "2" }, marking)).toBe(true);
    // Under #855's per-column sets these lit too: g3 × {1, 2}, g4 × {2, 8}, g5 × {8}.
    expect(isLit({ group: "g3", item: "1" }, marking)).toBe(false);
    expect(isLit({ group: "g4", item: "8" }, marking)).toBe(false);
    expect(isLit({ group: "g5", item: "8" }, marking)).toBe(false);
  });

  it("lights a row coarser than the marking when it contains a pick (D2, Spotfire's relation)", () => {
    // A per-group summary has no `item`: the picks project onto `group`.
    expect(isLit({ group: "g4", mean: "72" }, marking)).toBe(true);
    expect(isLit({ group: "g1", mean: "70" }, marking)).toBe(false);
    // Onto `item` alone: {8, 1, 2}.
    expect(isLit({ item: "2" }, marking)).toBe(true);
    expect(isLit({ item: "7" }, marking)).toBe(false);
  });

  it("does not light a row that shares NO key — an unrelated view stays dark", () => {
    expect(isLit({ tool: "t1" }, marking)).toBe(false);
  });

  it("lights nothing for an empty marking", () => {
    expect(isLit({ group: "g3" }, markingFrom(["group"], []))).toBe(false);
  });

  it("compares values as strings — opaque, no parsing", () => {
    const m = markingFrom(["item"], [["3"]]);
    expect(isLit({ item: "3" }, m)).toBe(true);
    expect(isLit({ item: "03" }, m)).toBe(false);
  });

  it("keeps a value holding a comma apart from a pair (the tuple text is joined by U+001F)", () => {
    const m = markingFrom(["p", "q"], [["a,b", "c"]]);
    expect(isLit({ p: "a", q: "b,c" }, m)).toBe(false);
    expect(isLit({ p: "a,b", q: "c" }, m)).toBe(true);
  });
});

describe("markingFrom / markingRows — the one shape on every wire", () => {
  it("sorts the keys and moves each row's values with them, so key order is not identity", () => {
    const a = markingFrom(["item", "group"], [["8", "g3"]]);
    const b = markingFrom(["group", "item"], [["g3", "8"]]);
    expect(a.keys).toEqual(["group", "item"]);
    expect(markingRows(a)).toEqual([["g3", "8"]]);
    expect(markingRows(a)).toEqual(markingRows(b));
  });

  it("drops duplicates and rows of the wrong length; rows come back sorted", () => {
    const m = markingFrom(["group", "item"], [["g5", "2"], ["g3", "8"], ["g5", "2"], ["g4"]]);
    expect(markingRows(m)).toEqual([
      ["g3", "8"],
      ["g5", "2"],
    ]);
    expect(markingSize(m)).toBe(2);
  });
});

describe("markedBy — what a count says it counts (D5)", () => {
  it("names the keys", () => {
    expect(markedBy(markingFrom(["item", "group"], [["1", "g1"]]))).toBe("by group, item");
  });
});

describe("projectOntoKeys — what a selection writes", () => {
  it("writes each picked row's key values as one tuple", () => {
    const rows = [
      { lot: "L1", wafer: "3", x: "0" },
      { lot: "L1", wafer: "4", x: "1" },
      { lot: "L2", wafer: "3", x: "2" },
    ];
    const m = projectOntoKeys(rows, ["lot", "wafer"])!;
    expect(m.keys).toEqual(["lot", "wafer"]);
    expect(markingRows(m)).toEqual([
      ["L1", "3"],
      ["L1", "4"],
      ["L2", "3"],
    ]);
    // Not the fourth combination (L2, 4).
    expect(isLit({ lot: "L2", wafer: "4" }, m)).toBe(false);
  });

  it("is null for a view without keys — it can be lit but cannot write", () => {
    expect(projectOntoKeys([{ lot: "L1" }], [])).toBeNull();
  });

  it("is an empty marking for an empty selection (the write that clears)", () => {
    expect(markingSize(projectOntoKeys([], ["lot"])!)).toBe(0);
  });

  it("skips a row that does not carry every key — it names no whole pick", () => {
    const m = projectOntoKeys([{ lot: "L1" }, { lot: "L2", wafer: "3" }], ["lot", "wafer"])!;
    expect(markingRows(m)).toEqual([["L2", "3"]]);
  });
});

describe("MarkingStore.set says whether the marking holds the write (#847/#848 PR 5 P41 row 27)", () => {
  it("is false when `ifEmpty` finds the marking occupied, and writes nothing", () => {
    const s = new MarkingStore();
    expect(s.set("picked", one("item", "p"), "/v/a.ai.yaml")).toBe(true);
    const onPicked = vi.fn();
    s.subscribe("picked", onPicked);
    expect(s.set("picked", one("item", "q"), "/v/b.ai.yaml", { ifEmpty: true })).toBe(false);
    expect(markingRows(s.get("picked")!.marking)).toEqual([["p"]]);
    expect(onPicked).not.toHaveBeenCalled();
  });

  it("is true for a seed on an empty marking", () => {
    const s = new MarkingStore();
    expect(s.set("picked", one("item", "q"), "/v/b.ai.yaml", { ifEmpty: true })).toBe(true);
    expect(s.get("picked")?.source).toBe("/v/b.ai.yaml");
  });

  it("is true, and silent, for the same write again: the marking holds it", () => {
    const s = new MarkingStore();
    s.set("picked", one("item", "p"), "/v/a.ai.yaml");
    const onPicked = vi.fn();
    s.subscribe("picked", onPicked);
    expect(s.set("picked", one("item", "p"), "/v/a.ai.yaml")).toBe(true);
    expect(onPicked).not.toHaveBeenCalled();
  });

  it("is true for a clear, and for a clear of a marking that holds nothing", () => {
    const s = new MarkingStore();
    expect(s.set("picked", null, null)).toBe(true);
    s.set("picked", one("item", "p"), null);
    expect(s.set("picked", one("item"), null)).toBe(true);
    expect(s.get("picked")).toBeUndefined();
  });
});

describe("MarkingStore", () => {
  it("reads back what was written, with the view that wrote it", () => {
    const s = new MarkingStore();
    s.set("fail", one("lot", "L1"), "/v/grid.ai.yaml");
    expect(markingRows(s.get("fail")!.marking)).toEqual([["L1"]]);
    expect(s.get("fail")?.source).toBe("/v/grid.ai.yaml");
    expect(s.get("other")).toBeUndefined();
  });

  it("notifies only the subscribers of the marking that changed", () => {
    const s = new MarkingStore();
    const onFail = vi.fn();
    const onOther = vi.fn();
    s.subscribe("fail", onFail);
    s.subscribe("other", onOther);
    s.set("fail", one("lot", "L1"), null);
    expect(onFail).toHaveBeenCalledTimes(1);
    expect(onOther).not.toHaveBeenCalled();
  });

  it("an empty marking clears the name", () => {
    const s = new MarkingStore();
    s.set("fail", one("lot", "L1"), null);
    s.set("fail", one("lot"), null);
    expect(s.get("fail")).toBeUndefined();
    s.set("fail", one("lot"), null);
    expect(s.get("fail")).toBeUndefined();
  });

  it("lists the names that hold a marking, for the send chips", () => {
    const s = new MarkingStore();
    const onNames = vi.fn();
    s.subscribeNames(onNames);
    s.set("b", one("lot", "L1"), null);
    s.set("a", one("lot", "L2"), null);
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
    s.set("fail", one("lot", "L1"), null);
    expect(cb).not.toHaveBeenCalled();
  });

  it("returns the same snapshot until the marking changes (useSyncExternalStore)", () => {
    const s = new MarkingStore();
    s.set("fail", one("lot", "L1"), null);
    expect(s.get("fail")).toBe(s.get("fail"));
    expect(s.names()).toBe(s.names());
  });

  it("offers one snapshot of every marking, new only after a write (the composer's chips)", () => {
    const s = new MarkingStore();
    const onAny = vi.fn();
    s.subscribeAll(onAny);
    const empty = s.snapshot();
    expect(s.snapshot()).toBe(empty);
    s.set("fail", one("lot", "L1"), "/v/a.ai.yaml");
    s.set("fail", one("lot", "L1", "L2"), "/v/a.ai.yaml");
    expect(onAny).toHaveBeenCalledTimes(2);
    const snap = s.snapshot();
    expect(snap).not.toBe(empty);
    expect([...snap.keys()]).toEqual(["fail"]);
    expect(markingSize(snap.get("fail")!.marking)).toBe(2);
  });

  it("writing what it already holds notifies no one (breaks write → redraw → write loops)", () => {
    // A chart redrawn with its new lit rows can report the same selection again;
    // if that write re-notified, it would redraw, report, write… forever.
    const s = new MarkingStore();
    s.set("fail", one("lot", "L1", "L2"), "/v/a.ai.yaml");
    const cb = vi.fn();
    s.subscribe("fail", cb);
    s.subscribeAll(cb);
    s.subscribeWrites(cb);
    const before = s.get("fail");
    s.set("fail", one("lot", "L2", "L1"), "/v/a.ai.yaml");
    expect(cb).not.toHaveBeenCalled();
    expect(s.get("fail")).toBe(before);
    // A different source is a different write.
    s.set("fail", one("lot", "L1", "L2"), "/v/b.ai.yaml");
    expect(cb).toHaveBeenCalled();
  });

  it("does not keep the caller's sets — a later mutation cannot change the marking", () => {
    const s = new MarkingStore();
    const rows = [["L1"]];
    s.set("fail", markingFrom(["lot"], rows), null);
    rows.push(["L2"]);
    expect(markingRows(s.get("fail")!.marking)).toEqual([["L1"]]);
  });
});
