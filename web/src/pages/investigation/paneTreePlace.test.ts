import { describe, expect, it } from "vitest";

import {
  type LayoutNode,
  layoutPaths,
  leaf,
  leafIds,
  type PaneNode,
  type PlacedGroup,
  placeLayout,
} from "./paneTree";

const L = (path: string): LayoutNode => ({ type: "leaf", path });
const row = (a: LayoutNode, b: LayoutNode, ratio = 0.5): LayoutNode => ({
  type: "split",
  dir: "row",
  ratio,
  a,
  b,
});
const col = (a: LayoutNode, b: LayoutNode, ratio = 0.5): LayoutNode => ({
  type: "split",
  dir: "col",
  ratio,
  a,
  b,
});

function group(id: string, paths: string[], active: string | null = paths[0] ?? null): PlacedGroup {
  return { id, tabs: paths.map((path) => ({ path })), activePath: active };
}

function ids() {
  let n = 100;
  return () => `g${n++}`;
}

/** The tab paths of each leaf, in visual order — what the user sees. */
function panes(tree: PaneNode, groups: Record<string, PlacedGroup>): string[][] {
  return leafIds(tree).map((id) => groups[id]!.tabs.map((t) => t.path));
}

describe("layoutPaths", () => {
  it("lists leaf paths in visual order", () => {
    expect(layoutPaths(row(col(L("/a"), L("/c")), L("/b")))).toEqual(["/a", "/c", "/b"]);
    expect(layoutPaths(L("/x"))).toEqual(["/x"]);
  });
});

describe("placeLayout — Q17", () => {
  it("one pane: the card's layout replaces it, existing tabs join its top-left leaf", () => {
    const out = placeLayout(
      leaf("g0"),
      { g0: group("g0", ["/notes.md", "/r.py"], "/r.py") },
      row(col(L("/grid.ai.yaml"), L("/scatter.ai.yaml")), L("/table.ai.yaml")),
      ids(),
    );
    expect(panes(out.tree, out.groups)).toEqual([
      ["/notes.md", "/r.py", "/grid.ai.yaml"],
      ["/scatter.ai.yaml"],
      ["/table.ai.yaml"],
    ]);
    // The card's shape survives: row at the root, col on the left.
    expect(out.tree.type === "split" && out.tree.dir).toBe("row");
    expect(out.tree.type === "split" && out.tree.a.type === "split" && out.tree.a.dir).toBe(
      "col",
    );
    // The top-left pane shows the card's file, not the tab that was active before.
    const topLeft = out.groups[leafIds(out.tree)[0]!]!;
    expect(topLeft.activePath).toBe("/grid.ai.yaml");
    expect(out.activeGroupId).toBe(topLeft.id);
  });

  it("one EMPTY pane: the result is exactly the card's layout", () => {
    const out = placeLayout(leaf("g0"), { g0: group("g0", []) }, row(L("/a"), L("/b")), ids());
    expect(panes(out.tree, out.groups)).toEqual([["/a"], ["/b"]]);
    expect(Object.keys(out.groups).sort()).toEqual(leafIds(out.tree).sort());
  });

  it("already split: the whole existing tree moves left, the card opens on the right", () => {
    const tree: PaneNode = {
      type: "split",
      dir: "col",
      ratio: 0.3,
      a: leaf("g0"),
      b: leaf("g1"),
    };
    const out = placeLayout(
      tree,
      { g0: group("g0", ["/x.md"]), g1: group("g1", ["/y.md"]) },
      col(L("/a"), L("/b"), 0.7),
      ids(),
    );
    expect(out.tree.type).toBe("split");
    if (out.tree.type !== "split") return;
    expect(out.tree.dir).toBe("row");
    // The old tree is untouched on the left, ratio and all.
    expect(out.tree.a).toEqual(tree);
    expect(out.tree.b.type === "split" && out.tree.b.ratio).toBe(0.7);
    expect(panes(out.tree, out.groups)).toEqual([["/x.md"], ["/y.md"], ["/a"], ["/b"]]);
    expect(out.groups[out.activeGroupId]!.activePath).toBe("/a");
  });

  it("a card file already open is moved into place, not duplicated (one pane)", () => {
    const out = placeLayout(
      leaf("g0"),
      { g0: group("g0", ["/notes.md", "/b"], "/b") },
      row(L("/a"), L("/b")),
      ids(),
    );
    expect(panes(out.tree, out.groups)).toEqual([["/notes.md", "/a"], ["/b"]]);
  });

  it("a card file already open elsewhere is moved, and a pane it emptied goes away", () => {
    const tree: PaneNode = { type: "split", dir: "row", ratio: 0.5, a: leaf("g0"), b: leaf("g1") };
    const out = placeLayout(
      tree,
      { g0: group("g0", ["/x.md"]), g1: group("g1", ["/a"]) },
      row(L("/a"), L("/b")),
      ids(),
    );
    expect(panes(out.tree, out.groups)).toEqual([["/x.md"], ["/a"], ["/b"]]);
    expect(out.groups.g1).toBeUndefined();
    // Every leaf has a group and every group has a leaf.
    expect(Object.keys(out.groups).sort()).toEqual(leafIds(out.tree).sort());
  });

  it("a moved-out active tab hands activity to a remaining tab", () => {
    const tree: PaneNode = { type: "split", dir: "row", ratio: 0.5, a: leaf("g0"), b: leaf("g1") };
    const out = placeLayout(
      tree,
      { g0: group("g0", ["/x.md", "/a"], "/a"), g1: group("g1", ["/y.md"]) },
      L("/a"),
      ids(),
    );
    expect(out.groups.g0!.activePath).toBe("/x.md");
  });

  it("split but every existing tab was a card file: only the card's layout remains", () => {
    const tree: PaneNode = { type: "split", dir: "row", ratio: 0.5, a: leaf("g0"), b: leaf("g1") };
    const out = placeLayout(
      tree,
      { g0: group("g0", ["/a"]), g1: group("g1", ["/b"]) },
      row(L("/a"), L("/b")),
      ids(),
    );
    expect(panes(out.tree, out.groups)).toEqual([["/a"], ["/b"]]);
    expect(Object.keys(out.groups).sort()).toEqual(leafIds(out.tree).sort());
  });

  it("keeps pin / preview flags of the tabs it carries over", () => {
    const out = placeLayout(
      leaf("g0"),
      { g0: { id: "g0", tabs: [{ path: "/n.md", pinned: true }], activePath: "/n.md" } },
      L("/a"),
      ids(),
    );
    expect(out.groups[leafIds(out.tree)[0]!]!.tabs).toEqual([
      { path: "/n.md", pinned: true },
      { path: "/a" },
    ]);
  });

  it("a card ratio outside the draggable range is clamped", () => {
    const out = placeLayout(leaf("g0"), { g0: group("g0", []) }, row(L("/a"), L("/b"), 1), ids());
    expect(out.tree.type === "split" && out.tree.ratio).toBeLessThan(1);
  });

  it("does not mutate its inputs", () => {
    const groups = { g0: group("g0", ["/a", "/n.md"]) };
    const snapshot = structuredClone(groups);
    placeLayout(leaf("g0"), groups, row(L("/a"), L("/b")), ids());
    expect(groups).toEqual(snapshot);
  });
});
