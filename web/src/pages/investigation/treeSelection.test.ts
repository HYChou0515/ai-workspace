import { describe, expect, it } from "vitest";

import type { TreeNode } from "./fileTree";
import { nextSelection, topLevel, visibleOrder } from "./treeSelection";

const order = ["/a", "/b", "/c", "/d"];
const plain = { ctrl: false, shift: false };
const ctrl = { ctrl: true, shift: false };
const shift = { ctrl: false, shift: true };
const ctrlShift = { ctrl: true, shift: true };

describe("nextSelection", () => {
  it("plain click selects only the clicked row", () => {
    expect(nextSelection({ selected: ["/a", "/c"], anchor: "/a" }, "/b", plain, order)).toEqual({
      selected: ["/b"],
      anchor: "/b",
    });
  });

  it("ctrl click toggles membership and moves the anchor", () => {
    const s1 = nextSelection({ selected: ["/b"], anchor: "/b" }, "/c", ctrl, order);
    expect(s1).toEqual({ selected: ["/b", "/c"], anchor: "/c" });
    const s2 = nextSelection(s1, "/b", ctrl, order);
    expect(s2.selected).toEqual(["/c"]);
    expect(s2.anchor).toBe("/b");
  });

  it("shift click selects the range from the anchor, keeping the anchor", () => {
    expect(nextSelection({ selected: ["/b"], anchor: "/b" }, "/d", shift, order)).toEqual({
      selected: ["/b", "/c", "/d"],
      anchor: "/b",
    });
  });

  it("shift range works upward too", () => {
    expect(nextSelection({ selected: ["/c"], anchor: "/c" }, "/a", shift, order).selected).toEqual([
      "/a",
      "/b",
      "/c",
    ]);
  });

  it("ctrl+shift adds the range to the existing selection", () => {
    const start = { selected: ["/a"], anchor: "/a" };
    expect(nextSelection(start, "/c", ctrlShift, order).selected.sort()).toEqual(["/a", "/b", "/c"]);
  });

  it("shift with no anchor falls back to single select", () => {
    expect(nextSelection({ selected: [], anchor: null }, "/b", shift, order)).toEqual({
      selected: ["/b"],
      anchor: "/b",
    });
  });
});

describe("topLevel", () => {
  it("drops descendants of a selected folder", () => {
    expect(topLevel(["/d", "/d/a.txt", "/c"])).toEqual(["/d", "/c"]);
  });

  it("keeps siblings that share no ancestor", () => {
    expect(topLevel(["/d/a.txt", "/d/b.txt"])).toEqual(["/d/a.txt", "/d/b.txt"]);
  });

  it("collapses a deep chain to the top folder", () => {
    expect(topLevel(["/a", "/a/b", "/a/b/c.txt"])).toEqual(["/a"]);
  });

  it("does not treat a name prefix as an ancestor", () => {
    // "/data" is not an ancestor of "/database.md"
    expect(topLevel(["/data", "/database.md"]).sort()).toEqual(["/data", "/database.md"]);
  });
});

describe("visibleOrder", () => {
  const tree: TreeNode[] = [
    {
      name: "data",
      path: "/data",
      isDir: true,
      children: [{ name: "x.csv", path: "/data/x.csv", isDir: false, children: [] }],
    },
    { name: "a.md", path: "/a.md", isDir: false, children: [] },
  ];

  it("lists rows depth-first", () => {
    expect(visibleOrder(tree, () => false)).toEqual(["/data", "/data/x.csv", "/a.md"]);
  });

  it("skips children of collapsed folders", () => {
    expect(visibleOrder(tree, (p) => p === "/data")).toEqual(["/data", "/a.md"]);
  });
});
