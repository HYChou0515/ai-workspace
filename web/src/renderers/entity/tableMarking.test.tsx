// @vitest-environment happy-dom
/**
 * `useTableMarking` as a plugin calls it through the SDK (#847/#848 PR 5 P2):
 * the cases a table's own UI cannot reach, because it disables its checkboxes
 * or always has a view file behind it.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { MarkingProvider } from "../../hooks/useMarking";
import { markingFrom, markingRows, MarkingStore } from "../../lib/markings";
import { useTableMarking } from "./tableMarking";

const ROWS = [{ lot: "L1" }, { lot: "L2" }, { lot: "L3" }];

function hook(store: MarkingStore, args: Partial<Parameters<typeof useTableMarking>[0]> = {}) {
  const wrapper = ({ children }: { children: ReactNode }) => <MarkingProvider store={store}>{children}</MarkingProvider>;
  return renderHook(() => useTableMarking({ marking: "fail", rows: ROWS, columns: ["lot"], ...args }), { wrapper });
}

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("useTableMarking's writer", () => {
  it("never writes — so never clears — when the table has no key column, even if a plugin calls it", () => {
    // A projection onto no column is `{}`, the write that clears the marking.
    const store = new MarkingStore();
    store.set("fail", markingFrom(["wafer"], [["W1"]]), "/views/chart.ai.yaml");
    const { result } = hook(store, { keys: ["wafer"] });
    expect(result.current.select!.enabled).toBe(false);
    act(() => result.current.select!.set(new Set([0])));
    expect(markingRows(store.get("fail")!.marking)).toEqual([["W1"]]);
  });
});

describe("the note, when selecting here marks nothing", () => {
  it("names the missing columns when the view's keys: are not in the table, before anything is marked", () => {
    // `keys:` decides what a selection writes, marked or not — so the reason
    // is the table, not a missing `keys:`.
    const { result } = hook(new MarkingStore(), { keys: ["wafer"] });
    expect(result.current.note).toBe("Selecting rows marks nothing: this table has none of fail's columns.");
  });

  it("asks for keys: when the view has none and nothing is marked yet", () => {
    const { result } = hook(new MarkingStore(), { keys: [] });
    expect(result.current.note).toBe(
      "Selecting rows marks nothing: add keys: to this view, or mark fail elsewhere first.",
    );
  });

  it("is quiet when a selection can write", () => {
    const { result } = hook(new MarkingStore(), { keys: ["lot"] });
    expect(result.current.note).toBeNull();
  });
});

describe("the table a selection was made in", () => {
  it("is known by its view file: a view with none is filtered like any other", () => {
    // A marking whose writer named no file (a preview) must not read as
    // "made here" to every other file-less table.
    const store = new MarkingStore();
    store.set("fail", markingFrom(["lot"], [["L2"]]), null);
    const { result } = hook(store, { source: null });
    expect(result.current.shown).toEqual([1]);
  });

  it("keeps every row, the marked ones highlighted", () => {
    const store = new MarkingStore();
    store.set("fail", markingFrom(["lot"], [["L2"]]), "/views/t.ai.yaml");
    const { result } = hook(store, { source: "/views/t.ai.yaml" });
    expect(result.current.shown).toEqual([0, 1, 2]);
    expect([...result.current.highlighted]).toEqual([1]);
  });
});

describe("a marking over two keys (#861 D1)", () => {
  const PAIRS = [
    { group: "g3", item: "8" },
    { group: "g3", item: "1" },
    { group: "g4", item: "1" },
    { group: "g4", item: "8" },
  ];
  const pairs = (store: MarkingStore, args: Partial<Parameters<typeof useTableMarking>[0]> = {}) =>
    hook(store, { rows: PAIRS, columns: ["group", "item"], keys: ["group", "item"], ...args });

  it("shows exactly the picked rows, not every combination of their values", () => {
    const store = new MarkingStore();
    store.set("fail", markingFrom(["group", "item"], [["g3", "8"], ["g4", "1"]]), "/views/gallery.ai.yaml");
    const { result } = pairs(store);
    // #855 showed all four: group {g3, g4} x item {8, 1}.
    expect(result.current.shown).toEqual([0, 2]);
  });

  it("ticking writes this table's picks and keeps a held pick none of its rows carries", () => {
    const store = new MarkingStore();
    store.set("fail", markingFrom(["group", "item"], [["g3", "8"], ["g9", "9"]]), "/views/gallery.ai.yaml");
    const { result } = pairs(store, { source: "/views/t.ai.yaml" });
    act(() => result.current.select!.set(new Set([2])));
    expect(markingRows(store.get("fail")!.marking)).toEqual([
      ["g4", "1"],
      ["g9", "9"],
    ]);
  });
});

describe("a table coarser than its marking (review #862 defect A1)", () => {
  // A per-group table beside a per-item view: the marking holds (group, item)
  // picks, the table only `group`.
  const GROUPS = [{ group: "g1" }, { group: "g2" }];
  const coarse = (store: MarkingStore, args: Partial<Parameters<typeof useTableMarking>[0]> = {}) =>
    hook(store, { rows: GROUPS, columns: ["group"], keys: [], source: "/views/groups.ai.yaml", ...args });
  const held = (store: MarkingStore) => {
    const m = store.get("fail")!.marking;
    return { keys: m.keys, rows: markingRows(m) };
  };

  it("unticking a group takes out only that group's picks: item-level picks and groups it lacks stay", () => {
    const store = new MarkingStore();
    store.set("fail", markingFrom(["group", "item"], [["g1", "1"], ["g2", "5"], ["g9", "3"]]), "/views/gallery.ai.yaml");
    const { result } = coarse(store);
    expect([...result.current.select!.checked]).toEqual([0, 1]);
    act(() => result.current.select!.set(new Set([0])));
    expect(held(store)).toEqual({
      keys: ["group", "item"],
      rows: [
        ["g1", "1"],
        ["g9", "3"],
      ],
    });
  });

  it("ticking a group with no pick keeps the groups it does not show, as whole groups (review #862 round 2)", () => {
    // (g9, 3) is in a group this table has no row for: it stays, said at the
    // table's keys once the marking becomes per-group -- as #855 carried it.
    const store = new MarkingStore();
    store.set("fail", markingFrom(["group", "item"], [["g1", "1"], ["g9", "3"]]), "/views/gallery.ai.yaml");
    const { result } = coarse(store);
    act(() => result.current.select!.set(new Set([0, 1])));
    expect(held(store)).toEqual({ keys: ["group"], rows: [["g1"], ["g2"], ["g9"]] });
  });

  it("ticking a group it holds no pick in writes at the table's own keys", () => {
    // (g2, *) cannot be said as item-level picks: the marking becomes per-group.
    const store = new MarkingStore();
    store.set("fail", markingFrom(["group", "item"], [["g1", "1"]]), "/views/gallery.ai.yaml");
    const { result } = coarse(store);
    act(() => result.current.select!.set(new Set([0, 1])));
    expect(held(store)).toEqual({ keys: ["group"], rows: [["g1"], ["g2"]] });
  });
});

describe("a table that has every key but writes fewer (`keys:` a subset; review #862 round 2)", () => {
  const ROWS2 = [
    { group: "g1", item: "a" },
    { group: "g1", item: "b" },
    { group: "g2", item: "c" },
    { group: "g1", item: "d" },
  ];
  const narrow = (store: MarkingStore) =>
    hook(store, { rows: ROWS2, columns: ["group", "item"], keys: ["group"], source: "/views/t.ai.yaml" });

  it("a tick is not a no-op: it writes at the table's keys", () => {
    const store = new MarkingStore();
    store.set("fail", markingFrom(["group", "item"], [["g1", "a"], ["g1", "b"], ["g2", "c"]]), "/views/gallery.ai.yaml");
    const { result } = narrow(store);
    // untick (g1, a): what `keys: [group]` says of rows b, c -- g1 and g2
    act(() => result.current.select!.set(new Set([1, 2])));
    const m = store.get("fail")!.marking;
    expect({ keys: m.keys, rows: markingRows(m) }).toEqual({ keys: ["group"], rows: [["g1"], ["g2"]] });
  });

  it("keeps the picks of groups it does not show, as whole groups (review #862 round 3)", () => {
    const store = new MarkingStore();
    store.set("fail", markingFrom(["group", "item"], [["g1", "a"], ["g9", "z"]]), "/views/gallery.ai.yaml");
    const { result } = narrow(store);
    act(() => result.current.select!.set(new Set([0, 2])));
    const m = store.get("fail")!.marking;
    expect({ keys: m.keys, rows: markingRows(m) }).toEqual({ keys: ["group"], rows: [["g1"], ["g2"], ["g9"]] });
  });
});
