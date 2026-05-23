import { describe, expect, it } from "vitest";

import {
  edgeForPoint,
  leaf,
  leafIds,
  type PaneNode,
  removeLeaf,
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

  it("edgeForPoint picks center in the middle, edges near the sides", () => {
    const rect = { left: 0, top: 0, width: 100, height: 100 };
    expect(edgeForPoint(50, 50, rect)).toBe("center");
    expect(edgeForPoint(5, 50, rect)).toBe("left");
    expect(edgeForPoint(95, 50, rect)).toBe("right");
    expect(edgeForPoint(50, 5, rect)).toBe("top");
    expect(edgeForPoint(50, 95, rect)).toBe("bottom");
  });
});
