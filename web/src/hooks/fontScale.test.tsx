// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  FONT_SCALE_MAX,
  FONT_SCALE_MIN,
  FontScaleProvider,
  initFontScale,
  readFontScale,
  useFontScale,
  useMonacoFontSize,
} from "./fontScale";

function rootFontSize(): string {
  return document.documentElement.style.fontSize;
}

const wrapper = ({ children }: { children: ReactNode }) => (
  <FontScaleProvider>{children}</FontScaleProvider>
);

describe("readFontScale", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it("defaults to 1.0 (100%) when nothing is stored", () => {
    expect(readFontScale()).toBe(1);
  });

  it("returns the stored scale", () => {
    localStorage.setItem("ui:font-scale", "1.25");
    expect(readFontScale()).toBe(1.25);
  });

  it("clamps an out-of-range stored value into [min, max]", () => {
    localStorage.setItem("ui:font-scale", "9");
    expect(readFontScale()).toBe(FONT_SCALE_MAX);
    localStorage.setItem("ui:font-scale", "0.1");
    expect(readFontScale()).toBe(FONT_SCALE_MIN);
  });

  it("falls back to the default for a non-numeric stored value", () => {
    localStorage.setItem("ui:font-scale", "huge");
    expect(readFontScale()).toBe(1);
  });
});

describe("initFontScale", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.style.fontSize = "";
  });
  afterEach(() => localStorage.clear());

  it("applies the stored scale to the document root as a percentage", () => {
    localStorage.setItem("ui:font-scale", "1.1");
    initFontScale();
    expect(rootFontSize()).toBe("110%");
  });

  it("applies 100% when nothing is stored", () => {
    initFontScale();
    expect(rootFontSize()).toBe("100%");
  });
});

describe("useFontScale", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.style.fontSize = "";
  });
  afterEach(() => localStorage.clear());

  it("exposes the current scale and applies it on mount", () => {
    localStorage.setItem("ui:font-scale", "1.2");
    const { result } = renderHook(() => useFontScale(), { wrapper });
    expect(result.current[0]).toBe(1.2);
    expect(rootFontSize()).toBe("120%");
  });

  it("persists and live-applies a new scale, clamping out-of-range input", () => {
    const { result } = renderHook(() => useFontScale(), { wrapper });
    act(() => result.current[1](1.3));
    expect(result.current[0]).toBe(1.3);
    expect(rootFontSize()).toBe("130%");
    expect(localStorage.getItem("ui:font-scale")).toBe("1.3");

    act(() => result.current[1](99));
    expect(result.current[0]).toBe(FONT_SCALE_MAX);
    expect(rootFontSize()).toBe(`${FONT_SCALE_MAX * 100}%`);
  });
});

describe("useMonacoFontSize", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it("scales a base px font size by the current scale, rounded to a whole px", () => {
    localStorage.setItem("ui:font-scale", "1.2");
    const { result } = renderHook(() => useMonacoFontSize(13), { wrapper });
    expect(result.current).toBe(16); // round(13 * 1.2) = round(15.6)
  });

  it("tracks live scale changes", () => {
    const both = renderHook(
      () => ({ size: useMonacoFontSize(13), api: useFontScale() }),
      { wrapper },
    );
    expect(both.result.current.size).toBe(13);
    act(() => both.result.current.api[1](1.5));
    expect(both.result.current.size).toBe(20); // round(13 * 1.5) = 19.5 -> 20
  });
});
