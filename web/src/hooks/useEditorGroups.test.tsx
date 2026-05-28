// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useEditorGroups } from "./useEditorGroups";

describe("useEditorGroups — empty panes are removed", () => {
  it("drops a split pane once its last tab is closed", () => {
    const { result } = renderHook(() => useEditorGroups(["/a.md", "/b.md"]));
    act(() => result.current.splitActive("right", "/a.md"));
    expect(result.current.isSplit).toBe(true);

    // empty the original group (g0)
    act(() => result.current.closeTab("g0", "/a.md"));
    act(() => result.current.closeTab("g0", "/b.md"));

    expect(result.current.isSplit).toBe(false);
  });

  it("removes the source pane after moving its only tab to a new edge pane", () => {
    const { result } = renderHook(() => useEditorGroups(["/a.md"]));
    // drag the sole tab of g0 to its own right edge (move, not copy)
    act(() => result.current.dropTabOnGroup("g0", "g0", "right", "/a.md", false));
    // net effect: still exactly one pane, holding /a.md
    expect(result.current.isSplit).toBe(false);
    expect(result.current.activeFile).toBe("/a.md");
  });

  it("keeps the sole pane even when its last tab closes", () => {
    const { result } = renderHook(() => useEditorGroups(["/a.md"]));
    act(() => result.current.closeTab("g0", "/a.md"));
    expect(result.current.isSplit).toBe(false);
    expect(result.current.activeFile).toBeNull();
  });
});

describe("useEditorGroups — split pane ratio", () => {
  it("setSplitRatio updates the addressed split", () => {
    const { result } = renderHook(() => useEditorGroups(["/a.md"]));
    act(() => result.current.splitActive("right", "/a.md"));
    // tree is now a single split at root: setSplitRatio([], 0.3)
    act(() => result.current.setSplitRatio([], 0.3));
    if (result.current.tree.type === "split") {
      expect(result.current.tree.ratio).toBeCloseTo(0.3);
    } else {
      throw new Error("expected a split at root after splitActive");
    }
  });

  it("setSplitRatio clamps to (0, 1)", () => {
    const { result } = renderHook(() => useEditorGroups(["/a.md"]));
    act(() => result.current.splitActive("right", "/a.md"));
    act(() => result.current.setSplitRatio([], -10));
    if (result.current.tree.type === "split") {
      expect(result.current.tree.ratio).toBeGreaterThan(0);
      expect(result.current.tree.ratio).toBeLessThan(0.1);
    }
  });

  it("setSplitRatio on a 2x2 inner split also updates the perpendicular sibling (linked)", () => {
    // Build a 2x2: (col(a, c)) | (col(b, d))
    const { result } = renderHook(() => useEditorGroups(["/a.md"]));
    act(() => result.current.splitActive("right", "/b.md"));   // a | b   active=b
    act(() => result.current.splitActive("down", "/c.md"));    // a | (b/c) — splits B
    // Now focus and split A:
    act(() => result.current.focusGroup("g0"));                 // a is g0
    act(() => result.current.splitActive("down", "/d.md"));    // (a/d) | (b/c)
    // Both inner splits should start at 0.5 (default)
    const before = result.current.tree;
    if (before.type !== "split" || before.a.type !== "split" || before.b.type !== "split") {
      throw new Error("expected 2x2");
    }
    expect(before.a.ratio).toBe(0.5);
    expect(before.b.ratio).toBe(0.5);

    // Drag the A-side inner divider:
    act(() => result.current.setSplitRatio(["a"], 0.3));
    const after = result.current.tree;
    if (after.type !== "split" || after.a.type !== "split" || after.b.type !== "split") {
      throw new Error("expected 2x2");
    }
    expect(after.a.ratio).toBeCloseTo(0.3);
    // …and the B-side ratio MUST follow (forced alignment).
    expect(after.b.ratio).toBeCloseTo(0.3);
  });
});
