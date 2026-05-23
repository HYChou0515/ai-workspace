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
