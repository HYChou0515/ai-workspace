import { describe, expect, it } from "vitest";

import {
  edgeForPoint,
  getNodeAt,
  leaf,
  leafIds,
  linkedSiblingPath,
  type PaneNode,
  removeLeaf,
  setRatioAt,
  splitLeaf,
} from "./paneTree";

describe("paneTree", () => {
  it("splits a leaf right → old left, new right", () => {
    const t = splitLeaf(leaf("a"), "a", "right", "b");
    expect(t.type).toBe("split");
    if (t.type === "split") {
      expect(t.dir).toBe("row");
      expect(leafIds(t)).toEqual(["a", "b"]);
    }
  });

  it("splits a leaf left → new on the left", () => {
    expect(leafIds(splitLeaf(leaf("a"), "a", "left", "b"))).toEqual(["b", "a"]);
  });

  it("splits bottom → col dir, new below", () => {
    const t = splitLeaf(leaf("a"), "a", "bottom", "b");
    if (t.type === "split") {
      expect(t.dir).toBe("col");
      expect(leafIds(t)).toEqual(["a", "b"]);
    }
  });

  it("nests: splitting one leaf leaves siblings untouched", () => {
    let t: PaneNode = splitLeaf(leaf("a"), "a", "right", "b"); // [a|b]
    t = splitLeaf(t, "b", "bottom", "c"); // a | (b / c)
    expect(leafIds(t)).toEqual(["a", "b", "c"]);
    if (t.type === "split") {
      expect(t.dir).toBe("row");
      expect(t.a.type).toBe("leaf");
      expect(t.b.type).toBe("split");
    }
  });

  it("removeLeaf collapses the parent to the sibling", () => {
    const t = removeLeaf(splitLeaf(leaf("a"), "a", "right", "b"), "b");
    expect(t).toEqual(leaf("a"));
  });

  it("removeLeaf on the sole root leaf is a no-op", () => {
    expect(removeLeaf(leaf("a"), "a")).toEqual(leaf("a"));
  });

  it("splitLeaf initialises ratio to 0.5 (even split)", () => {
    const t = splitLeaf(leaf("a"), "a", "right", "b");
    if (t.type === "split") expect(t.ratio).toBe(0.5);
  });

  it("setRatioAt updates the addressed split, leaving siblings untouched", () => {
    // [a | (b / c)]: root is row, b/c is col under root.b
    let t: PaneNode = splitLeaf(leaf("a"), "a", "right", "b");
    t = splitLeaf(t, "b", "bottom", "c");
    // address the ROOT split (empty path)
    const t1 = setRatioAt(t, [], 0.3);
    if (t1.type === "split") {
      expect(t1.ratio).toBe(0.3);
      // the inner b/c split kept its 0.5
      if (t1.b.type === "split") expect(t1.b.ratio).toBe(0.5);
    }
    // address the inner split via ["b"]
    const t2 = setRatioAt(t, ["b"], 0.7);
    if (t2.type === "split" && t2.b.type === "split") {
      expect(t2.b.ratio).toBe(0.7);
      expect(t2.ratio).toBe(0.5); // outer untouched
    }
  });

  it("setRatioAt clamps to (0, 1) so a pane can't be dragged to 0/100%", () => {
    const t = splitLeaf(leaf("a"), "a", "right", "b");
    const lo = setRatioAt(t, [], -0.5);
    const hi = setRatioAt(t, [], 1.5);
    if (lo.type === "split") expect(lo.ratio).toBeGreaterThan(0);
    if (hi.type === "split") expect(hi.ratio).toBeLessThan(1);
  });

  it("setRatioAt is a no-op when the path doesn't address a split", () => {
    const t = leaf("a");
    expect(setRatioAt(t, [], 0.3)).toEqual(t); // leaf has no ratio
    const t2 = splitLeaf(leaf("a"), "a", "right", "b");
    // path ["a"] addresses a LEAF (a) — should be a no-op
    expect(setRatioAt(t2, ["a"], 0.3)).toEqual(t2);
  });

  it("getNodeAt walks 'a'/'b' from root, returns null on overshoot", () => {
    let t: PaneNode = splitLeaf(leaf("a"), "a", "right", "b");
    t = splitLeaf(t, "b", "bottom", "c"); // split(row, a, split(col, b, c))
    expect(getNodeAt(t, [])).toBe(t);
    expect(getNodeAt(t, ["a"])).toEqual(leaf("a"));
    expect(getNodeAt(t, ["b", "a"])).toEqual(leaf("b"));
    expect(getNodeAt(t, ["b", "b"])).toEqual(leaf("c"));
    // can't descend into a leaf
    expect(getNodeAt(t, ["a", "a"])).toBeNull();
  });

  it("linkedSiblingPath finds a perpendicular-split sibling at the same parent", () => {
    // Build 2x2: split(row, split(col, a, c), split(col, b, d))
    let t: PaneNode = splitLeaf(leaf("a"), "a", "right", "b");
    t = splitLeaf(t, "a", "bottom", "c");
    t = splitLeaf(t, "b", "bottom", "d");
    expect(linkedSiblingPath(t, ["a"])).toEqual(["b"]);
    expect(linkedSiblingPath(t, ["b"])).toEqual(["a"]);
  });

  it("linkedSiblingPath returns null when there's no sibling split (左一右二)", () => {
    // split(row, leaf("a"), split(col, b, c)) — A is leaf, no link
    let t: PaneNode = splitLeaf(leaf("a"), "a", "right", "b");
    t = splitLeaf(t, "b", "bottom", "c");
    expect(linkedSiblingPath(t, ["b"])).toBeNull();
  });

  it("linkedSiblingPath returns null at root (no parent)", () => {
    const t: PaneNode = splitLeaf(leaf("a"), "a", "right", "b");
    expect(linkedSiblingPath(t, [])).toBeNull();
  });

  it("linkedSiblingPath returns null when sibling split has a different direction", () => {
    // split(row, split(col, a, c), split(row, b, d)) — sibling B is row, not col
    // Can't build this with splitLeaf alone, so construct manually:
    const t: PaneNode = {
      type: "split",
      dir: "row",
      ratio: 0.5,
      a: { type: "split", dir: "col", ratio: 0.5, a: leaf("a"), b: leaf("c") },
      b: { type: "split", dir: "row", ratio: 0.5, a: leaf("b"), b: leaf("d") },
    };
    expect(linkedSiblingPath(t, ["a"])).toBeNull();
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
