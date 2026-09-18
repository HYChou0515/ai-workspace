// @vitest-environment happy-dom
/**
 * The overview's view — cards or table — remembered per browser
 * (`docs/plan-wui-overview-icon-favourites.md`, the cards amendment).
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { readWuiView, useWuiView, writeWuiView } from "./wuiView";

const KEY = "rca.wuiView";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("readWuiView / writeWuiView", () => {
  it("is cards by default, and for anything that is not one of the two words", () => {
    expect(readWuiView()).toBe("cards");
    localStorage.setItem(KEY, "grid");
    expect(readWuiView()).toBe("cards");
    localStorage.setItem(KEY, "");
    expect(readWuiView()).toBe("cards");
  });

  it("remembers the table, and cards again", () => {
    writeWuiView("table");
    expect(readWuiView()).toBe("table");
    expect(localStorage.getItem(KEY)).toBe("table");
    writeWuiView("cards");
    expect(readWuiView()).toBe("cards");
  });

  it("survives a storage that throws — the choice just is not sticky", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readWuiView()).toBe("cards");
    expect(() => writeWuiView("table")).not.toThrow();
  });
});

describe("useWuiView", () => {
  it("starts from storage, re-renders on set, and writes through", () => {
    writeWuiView("table");
    const { result } = renderHook(() => useWuiView());
    expect(result.current[0]).toBe("table");

    act(() => result.current[1]("cards"));

    expect(result.current[0]).toBe("cards");
    expect(readWuiView()).toBe("cards");
  });
});
