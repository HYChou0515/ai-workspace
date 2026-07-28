// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BREAKPOINTS } from "../lib/breakpoints";
import { shellIsNarrow, useContainerWidth } from "./useContainerWidth";

/**
 * #fe-responsive — `useIsNarrow()` asks the VIEWPORT whether the layout is
 * narrow, but the workspace shell does not own the viewport: a chat-first App
 * puts a 240px chat rail in front of it. Measured in a real browser at 768px,
 * the shell had 528px to work with and still believed it was wide, so it laid
 * out activity bar (50) + file tree (260) + editor (min 360) + chat (380) into
 * 528px. Everything past 528px was silently clipped by `page-item`.
 *
 * The layout decision has to be made from the width the shell actually has.
 */
describe("shellIsNarrow", () => {
  it("falls back to the viewport verdict before the container has been measured", () => {
    expect(shellIsNarrow(0, true)).toBe(true);
    expect(shellIsNarrow(0, false)).toBe(false);
    expect(shellIsNarrow(null, true)).toBe(true);
  });

  it("calls a shell narrow when its own box is narrow, even on a wide viewport", () => {
    // The chat-first case: 768px viewport minus the 240px rail.
    expect(shellIsNarrow(528, false)).toBe(true);
  });

  it("calls a shell wide when its own box is wide, even if the viewport query says narrow", () => {
    expect(shellIsNarrow(1200, true)).toBe(false);
  });

  // The shell's threshold is NOT the app-wide `narrow` (768). That number sizes
  // the KB/dashboard grids; this shell has to fit four columns side by side —
  // activity bar 50 + file tree min 180 + editor min 360 + chat min 280 = 870.
  // Measured in a real browser at exactly 768, the shell still called itself
  // wide and squeezed the editor to ~190px (markdown wrapping at two glyphs a
  // line) while the item title in the top bar was down to 6px.
  it("uses the width its own columns need, not the app-wide narrow breakpoint", () => {
    expect(BREAKPOINTS.shell).toBeGreaterThan(BREAKPOINTS.narrow);
    expect(shellIsNarrow(BREAKPOINTS.narrow, false)).toBe(true);
    expect(shellIsNarrow(820, false)).toBe(true);
  });

  it("treats its own breakpoint as wide, one px below as narrow", () => {
    expect(shellIsNarrow(BREAKPOINTS.shell, false)).toBe(false);
    expect(shellIsNarrow(BREAKPOINTS.shell - 1, false)).toBe(true);
  });
});

describe("useContainerWidth", () => {
  const realRO = globalThis.ResizeObserver;
  afterEach(() => {
    globalThis.ResizeObserver = realRO;
  });

  /** A ResizeObserver stub that lets a test push a width through. */
  function stubObserver() {
    const callbacks: ResizeObserverCallback[] = [];
    globalThis.ResizeObserver = class {
      constructor(cb: ResizeObserverCallback) {
        callbacks.push(cb);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
    return {
      emit(width: number) {
        for (const cb of callbacks) {
          cb(
            [{ contentRect: { width } } as unknown as ResizeObserverEntry],
            {} as ResizeObserver,
          );
        }
      },
    };
  }

  it("reports 0 until the element is attached and measured", () => {
    stubObserver();
    const { result } = renderHook(() => useContainerWidth<HTMLDivElement>());
    expect(result.current[1]).toBe(0);
  });

  it("reports the observed width once the element resizes", () => {
    const ro = stubObserver();
    const { result } = renderHook(() => useContainerWidth<HTMLDivElement>());
    act(() => {
      result.current[0](document.createElement("div"));
    });
    act(() => ro.emit(528));
    expect(result.current[1]).toBe(528);
  });

  it("keeps tracking the element across later resizes", () => {
    const ro = stubObserver();
    const { result } = renderHook(() => useContainerWidth<HTMLDivElement>());
    act(() => {
      result.current[0](document.createElement("div"));
    });
    act(() => ro.emit(528));
    act(() => ro.emit(1280));
    expect(result.current[1]).toBe(1280);
  });

  it("degrades to 0 (viewport fallback) where ResizeObserver is unavailable", () => {
    // @ts-expect-error deliberately removing the global for the fallback path
    globalThis.ResizeObserver = undefined;
    const { result } = renderHook(() => useContainerWidth<HTMLDivElement>());
    act(() => {
      result.current[0](document.createElement("div"));
    });
    expect(result.current[1]).toBe(0);
  });

  it("disconnects the observer when the element goes away", () => {
    const disconnect = vi.fn();
    globalThis.ResizeObserver = class {
      constructor(_cb: ResizeObserverCallback) {}
      observe() {}
      unobserve() {}
      disconnect = disconnect;
    } as unknown as typeof ResizeObserver;
    const { result } = renderHook(() => useContainerWidth<HTMLDivElement>());
    act(() => {
      result.current[0](document.createElement("div"));
    });
    act(() => {
      result.current[0](null);
    });
    expect(disconnect).toHaveBeenCalled();
  });
});
