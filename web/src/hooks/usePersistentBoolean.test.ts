// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { usePersistentBoolean } from "./usePersistentBoolean";

describe("usePersistentBoolean", () => {
  beforeEach(() => localStorage.clear());

  it("returns the initial value when localStorage is empty", () => {
    const a = renderHook(() => usePersistentBoolean("k", true));
    expect(a.result.current[0]).toBe(true);
    const b = renderHook(() => usePersistentBoolean("k2", false));
    expect(b.result.current[0]).toBe(false);
  });

  it("persists boolean updates to localStorage", () => {
    const { result } = renderHook(() => usePersistentBoolean("k", true));
    act(() => result.current[1](false));
    expect(result.current[0]).toBe(false);
    expect(localStorage.getItem("k")).toBe("false");
  });

  it("reads a stored value back, overriding the initial default", () => {
    localStorage.setItem("k", "true");
    const { result } = renderHook(() => usePersistentBoolean("k", false));
    // The stored `true` wins over the `false` first-time default — this is how
    // the per-App layout preference survives reloads (#159).
    expect(result.current[0]).toBe(true);
  });

  it("supports a functional toggle updater", () => {
    const { result } = renderHook(() => usePersistentBoolean("k", false));
    act(() => result.current[1]((prev) => !prev));
    expect(result.current[0]).toBe(true);
    expect(localStorage.getItem("k")).toBe("true");
  });
});
