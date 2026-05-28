// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { usePersistentNumber } from "./usePersistentNumber";

describe("usePersistentNumber", () => {
  beforeEach(() => localStorage.clear());

  it("returns the initial value when localStorage is empty", () => {
    const { result } = renderHook(() => usePersistentNumber("k", 100));
    expect(result.current[0]).toBe(100);
  });

  it("persists numeric updates to localStorage", () => {
    const { result } = renderHook(() => usePersistentNumber("k", 100, 0, 999));
    act(() => result.current[1](250));
    expect(result.current[0]).toBe(250);
    expect(localStorage.getItem("k")).toBe("250");
  });

  it("clamps to [min, max] on numeric set", () => {
    const { result } = renderHook(() => usePersistentNumber("k", 100, 10, 200));
    act(() => result.current[1](500));
    expect(result.current[0]).toBe(200);
    act(() => result.current[1](-1));
    expect(result.current[0]).toBe(10);
  });

  // The stale-closure repro: ResizeDivider fires onResize many times in a
  // single tick (faster than React can re-render), so each handler closes over
  // the same stale value. The functional updater form is the standard fix —
  // `prev` is always the latest committed value, so deltas accumulate.
  it("functional updater accumulates across rapid-fire calls (stale-closure fix)", () => {
    const { result } = renderHook(() => usePersistentNumber("k", 100, 0, 999));
    // Three rapid calls inside the same act() — the closure of `result.current`
    // never advances between them. Without functional support this would
    // collapse to `100 + 5` (only the last delta survives, against `100`).
    act(() => {
      result.current[1]((prev) => prev + 3);
      result.current[1]((prev) => prev + 4);
      result.current[1]((prev) => prev + 5);
    });
    expect(result.current[0]).toBe(112);
    expect(localStorage.getItem("k")).toBe("112");
  });

  it("clamps the result of a functional updater too", () => {
    const { result } = renderHook(() => usePersistentNumber("k", 100, 10, 200));
    act(() => result.current[1]((prev) => prev + 9999));
    expect(result.current[0]).toBe(200);
  });
});
