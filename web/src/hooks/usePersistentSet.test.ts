// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { usePersistentDeque, usePersistentSet } from "./usePersistentSet";

afterEach(() => localStorage.clear());

describe("usePersistentSet — a key change loads that key's set", () => {
  it("does not write the previous key's state under the new key", () => {
    // The tree keeps its collapsed / opened folders under a per-item key.
    // Navigating item A → item B without a remount used to save A's set under
    // B's key — and with the "opened lazy folders" set, that means B's
    // `node_modules` expands and fetches on arrival for no reason.
    localStorage.setItem("s:B", JSON.stringify(["/b-only"]));
    const { result, rerender } = renderHook(({ key }) => usePersistentSet(key), {
      initialProps: { key: "s:A" },
    });
    act(() => result.current.toggle("/a-only"));
    expect(JSON.parse(localStorage.getItem("s:A") ?? "[]")).toEqual(["/a-only"]);

    rerender({ key: "s:B" });

    expect(result.current.has("/b-only")).toBe(true);
    expect(result.current.has("/a-only")).toBe(false);
    expect(JSON.parse(localStorage.getItem("s:B") ?? "[]")).toEqual(["/b-only"]);
    expect(JSON.parse(localStorage.getItem("s:A") ?? "[]")).toEqual(["/a-only"]);
  });

  it("keeps saving under the key it loaded from after the switch", () => {
    const { result, rerender } = renderHook(({ key }) => usePersistentSet(key), {
      initialProps: { key: "s:A" },
    });
    rerender({ key: "s:B" });
    act(() => result.current.toggle("/x"));
    expect(JSON.parse(localStorage.getItem("s:B") ?? "[]")).toEqual(["/x"]);
    expect(localStorage.getItem("s:A")).toBe("[]");
  });
});

describe("usePersistentDeque — the same rule", () => {
  it("does not write the previous key's list under the new key", () => {
    localStorage.setItem("d:B", JSON.stringify(["b1"]));
    const { result, rerender } = renderHook(({ key }) => usePersistentDeque(key), {
      initialProps: { key: "d:A" },
    });
    act(() => result.current.push("a1"));
    rerender({ key: "d:B" });
    expect(result.current.values).toEqual(["b1"]);
    expect(JSON.parse(localStorage.getItem("d:B") ?? "[]")).toEqual(["b1"]);
  });
});
