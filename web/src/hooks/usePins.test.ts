// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { usePinned, useRecentlyViewed } from "./usePins";

beforeEach(() => localStorage.clear());

describe("usePinned", () => {
  it("toggles a pin and persists it across remounts (per slug)", () => {
    const { result, unmount } = renderHook(() => usePinned("rca"));
    expect(result.current.isPinned("i1")).toBe(false);
    act(() => result.current.toggle("i1"));
    expect(result.current.isPinned("i1")).toBe(true);
    unmount();

    // a fresh mount reads it back from localStorage
    const { result: r2 } = renderHook(() => usePinned("rca"));
    expect(r2.current.isPinned("i1")).toBe(true);
    // a different App's pins are isolated
    const { result: other } = renderHook(() => usePinned("yield"));
    expect(other.current.isPinned("i1")).toBe(false);
  });
});

describe("useRecentlyViewed", () => {
  it("keeps most-recent-first, deduped and capped", () => {
    const { result } = renderHook(() => useRecentlyViewed("rca"));
    act(() => result.current.record("a"));
    act(() => result.current.record("b"));
    act(() => result.current.record("a")); // re-view moves to front, no dup
    expect(result.current.recent).toEqual(["a", "b"]);
  });
});
