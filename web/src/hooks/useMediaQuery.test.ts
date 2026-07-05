// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useIsNarrow, useMediaQuery } from "./useMediaQuery";

/** A controllable window.matchMedia: `set(true)` flips the match and notifies
 * subscribers, mimicking a viewport crossing the breakpoint. */
function stubMatchMedia() {
  let matches = false;
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const spy = vi.fn((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: (_t: string, cb: (e: MediaQueryListEvent) => void) => listeners.add(cb),
    removeEventListener: (_t: string, cb: (e: MediaQueryListEvent) => void) => listeners.delete(cb),
    addListener: (cb: (e: MediaQueryListEvent) => void) => listeners.add(cb),
    removeListener: (cb: (e: MediaQueryListEvent) => void) => listeners.delete(cb),
    dispatchEvent: () => true,
  }));
  window.matchMedia = spy as unknown as typeof window.matchMedia;
  return {
    spy,
    set(v: boolean) {
      matches = v;
      act(() => listeners.forEach((cb) => cb({ matches } as MediaQueryListEvent)));
    },
  };
}

afterEach(() => vi.restoreAllMocks());

describe("useMediaQuery", () => {
  it("returns the current match state for the query", () => {
    stubMatchMedia();
    const { result } = renderHook(() => useMediaQuery("(max-width: 767px)"));
    expect(result.current).toBe(false);
  });

  it("re-renders when the viewport crosses the query boundary", () => {
    const mm = stubMatchMedia();
    const { result } = renderHook(() => useMediaQuery("(max-width: 767px)"));
    expect(result.current).toBe(false);
    mm.set(true);
    expect(result.current).toBe(true);
    mm.set(false);
    expect(result.current).toBe(false);
  });

  it("unsubscribes on unmount (no leaked listener)", () => {
    const mm = stubMatchMedia();
    const { unmount } = renderHook(() => useMediaQuery("(max-width: 767px)"));
    unmount();
    // Flipping after unmount must not throw / update a torn-down hook.
    expect(() => mm.set(true)).not.toThrow();
  });
});

describe("useIsNarrow", () => {
  it("queries the shared 768px narrow breakpoint", () => {
    const mm = stubMatchMedia();
    renderHook(() => useIsNarrow());
    expect(mm.spy).toHaveBeenCalledWith("(max-width: 767px)");
  });
});
