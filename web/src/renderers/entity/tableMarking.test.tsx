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
import { MarkingStore } from "../../lib/markings";
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
    store.set("fail", { wafer: new Set(["W1"]) }, "/views/chart.ai.yaml");
    const { result } = hook(store, { keys: ["wafer"] });
    expect(result.current.select!.enabled).toBe(false);
    act(() => result.current.select!.set(new Set([0])));
    expect([...store.get("fail")!.marking.wafer!]).toEqual(["W1"]);
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
    store.set("fail", { lot: new Set(["L2"]) }, null);
    const { result } = hook(store, { source: null });
    expect(result.current.shown).toEqual([1]);
  });

  it("keeps every row, the marked ones highlighted", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2"]) }, "/views/t.ai.yaml");
    const { result } = hook(store, { source: "/views/t.ai.yaml" });
    expect(result.current.shown).toEqual([0, 1, 2]);
    expect([...result.current.highlighted]).toEqual([1]);
  });
});
