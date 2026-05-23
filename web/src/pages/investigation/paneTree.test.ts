import { describe, expect, it } from "vitest";

import {
  edgeForPoint,
  type PaneNode,
  leaf,
  leaves,
  removeLeaf,
  setLeafPath,
  splitLeaf,
} from "./paneTree";

const ids = (n: PaneNode) => leaves(n).map((l) => l.id);

describe("paneTree", () => {
  it("splits a leaf right → old left, new right", () => {
    const t = splitLeaf(leaf("a", "/a"), "a", "right", "b", "/b");
    expect(t.type).toBe("split");
    if (t.type === "split") {
      expect(t.dir).toBe("row");
      expect(ids(t)).toEqual(["a", "b"]);
    }
  });

  it("splits a leaf left → new on the left", () => {
    const t = splitLeaf(leaf("a", "/a"), "a", "left", "b", "/b");
    expect(ids(t)).toEqual(["b", "a"]);
  });

  it("splits bottom → col dir, new below", () => {
    const t = splitLeaf(leaf("a", "/a"), "a", "bottom", "b", "/b");
    if (t.type === "split") {
      expect(t.dir).toBe("col");
      expect(ids(t)).toEqual(["a", "b"]);
    }
  });

  it("nests: splitting one leaf leaves siblings untouched", () => {
    let t: PaneNode = splitLeaf(leaf("a", "/a"), "a", "right", "b", "/b"); // [a|b]
    t = splitLeaf(t, "b", "bottom", "c", "/c"); // a | (b / c)
    expect(ids(t)).toEqual(["a", "b", "c"]);
    if (t.type === "split") {
      expect(t.dir).toBe("row");
      expect(t.a.type).toBe("leaf"); // a stays a plain leaf
      expect(t.b.type).toBe("split"); // b became a vertical split
    }
  });

  it("removeLeaf collapses the parent to the sibling", () => {
    let t: PaneNode = splitLeaf(leaf("a", "/a"), "a", "right", "b", "/b");
    t = removeLeaf(t, "b");
    expect(t).toEqual(leaf("a", "/a"));
  });

  it("removeLeaf on the sole root leaf is a no-op", () => {
    expect(removeLeaf(leaf("a", "/a"), "a")).toEqual(leaf("a", "/a"));
  });

  it("setLeafPath updates only the target leaf", () => {
    const t = setLeafPath(splitLeaf(leaf("a", "/a"), "a", "right", "b", "/b"), "a", "/z");
    expect(leaves(t).find((l) => l.id === "a")?.path).toBe("/z");
    expect(leaves(t).find((l) => l.id === "b")?.path).toBe("/b");
  });

  it("edgeForPoint picks center in the middle, edges near the sides", () => {
    const rect = { left: 0, top: 0, width: 100, height: 100 };
    expect(edgeForPoint(50, 50, rect)).toBe("center");
    expect(edgeForPoint(5, 50, rect)).toBe("left");
    expect(edgeForPoint(95, 50, rect)).toBe("right");
    expect(edgeForPoint(50, 5, rect)).toBe("top");
    expect(edgeForPoint(50, 95, rect)).toBe("bottom");
  });
});
