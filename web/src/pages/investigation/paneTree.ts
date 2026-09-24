/**
 * Recursive editor-group tree (VSCode editor groups). Nodes are purely
 * structural — a leaf carries only a group `id`; the group's tabs/active
 * file live in a separate `Map<id, Group>` owned by the shell. Edge drops
 * split the targeted leaf in place, so splitting one group never disturbs
 * a sibling (true nesting).
 */

export type Edge = "left" | "right" | "top" | "bottom" | "center";

export type PaneLeaf = { type: "leaf"; id: string };
/** ratio = A's share of the split (0..1); B gets 1 - ratio. */
export type PaneSplit = {
  type: "split";
  dir: "row" | "col";
  ratio: number;
  a: PaneNode;
  b: PaneNode;
};
export type PaneNode = PaneLeaf | PaneSplit;

/** Min/max share for either side of a split (~5%/95%) so a pane can't be
 * dragged to 0 / 100 % and "vanish". */
export const MIN_RATIO = 0.05;
export const MAX_RATIO = 0.95;

export function leaf(id: string): PaneLeaf {
  return { type: "leaf", id };
}

/** Leaf ids in visual order (left→right / top→bottom). */
export function leafIds(node: PaneNode): string[] {
  return node.type === "leaf" ? [node.id] : [...leafIds(node.a), ...leafIds(node.b)];
}

export function hasLeaf(node: PaneNode, id: string): boolean {
  return leafIds(node).includes(id);
}

/** Split leaf `id` along `edge`, placing a new leaf (`newId`) on the edge
 * side. "center" is a no-op (callers open-in-place instead). */
export function splitLeaf(
  node: PaneNode,
  id: string,
  edge: Exclude<Edge, "center">,
  newId: string,
): PaneNode {
  if (node.type === "leaf") {
    if (node.id !== id) return node;
    const dir = edge === "left" || edge === "right" ? "row" : "col";
    const fresh = leaf(newId);
    const newFirst = edge === "left" || edge === "top";
    return {
      type: "split",
      dir,
      ratio: 0.5,
      a: newFirst ? fresh : node,
      b: newFirst ? node : fresh,
    };
  }
  return {
    ...node,
    a: splitLeaf(node.a, id, edge, newId),
    b: splitLeaf(node.b, id, edge, newId),
  };
}

/** Remove leaf `id`; its parent split collapses to the sibling. Returns the
 * unchanged root if it's the sole leaf. */
export function removeLeaf(node: PaneNode, id: string): PaneNode {
  if (node.type === "leaf") return node;
  if (node.a.type === "leaf" && node.a.id === id) return node.b;
  if (node.b.type === "leaf" && node.b.id === id) return node.a;
  return { ...node, a: removeLeaf(node.a, id), b: removeLeaf(node.b, id) };
}

/** Replace `ratio` on the split addressed by `path` (an array of "a"/"b" from
 * root). No-op if the path doesn't land on a split. Used by the divider
 * between split panes to adjust its share. */
export function setRatioAt(node: PaneNode, path: ("a" | "b")[], ratio: number): PaneNode {
  if (path.length === 0) {
    if (node.type !== "split") return node;
    const clamped = Math.max(MIN_RATIO, Math.min(MAX_RATIO, ratio));
    return { ...node, ratio: clamped };
  }
  if (node.type !== "split") return node;
  const [head, ...rest] = path;
  if (head === "a") return { ...node, a: setRatioAt(node.a, rest, ratio) };
  if (head === "b") return { ...node, b: setRatioAt(node.b, rest, ratio) };
  return node;
}

/** Walk from root along an "a"/"b" path. Returns null if the path tries to
 * descend into a leaf. */
export function getNodeAt(root: PaneNode, path: ("a" | "b")[]): PaneNode | null {
  let node: PaneNode = root;
  for (const seg of path) {
    if (node.type !== "split") return null;
    node = seg === "a" ? node.a : node.b;
  }
  return node;
}

/** Find the path of a sibling split that should be LINKED to the one at
 * `path` — i.e., its ratio is forced to stay equal so the dividers between
 * 4 panes stay aligned and a cross/T handle is always meaningful.
 *
 * A split S at `path` has a linked sibling iff:
 *   - S has a parent (path is non-empty)
 *   - The sibling at the parent is also a split
 *   - Both S and the sibling are PERPENDICULAR to the parent
 *     (i.e. parent is row → both children are col splits)
 *
 * The 2x2 case has this; "左一右二" (sibling is a leaf) does not. */
export function linkedSiblingPath(
  root: PaneNode,
  path: ("a" | "b")[],
): ("a" | "b")[] | null {
  if (path.length === 0) return null;
  const parentPath = path.slice(0, -1);
  const parent = getNodeAt(root, parentPath);
  if (!parent || parent.type !== "split") return null;
  const me = getNodeAt(root, path);
  if (!me || me.type !== "split") return null;
  if (me.dir === parent.dir) return null; // parallel to parent — not a perp child
  const lastSeg = path[path.length - 1] as "a" | "b";
  const sibling = lastSeg === "a" ? parent.b : parent.a;
  if (sibling.type !== "split") return null;
  if (sibling.dir !== me.dir) return null;
  return [...parentPath, lastSeg === "a" ? "b" : "a"];
}

/** Map a pointer position within a rect to a drop edge. The center 40% box
 * is "open here"; the outer margins pick a direction. */
export function edgeForPoint(
  x: number,
  y: number,
  rect: { left: number; top: number; width: number; height: number },
): Edge {
  const fx = (x - rect.left) / rect.width;
  const fy = (y - rect.top) / rect.height;
  if (fx >= 0.3 && fx <= 0.7 && fy >= 0.3 && fy <= 0.7) return "center";
  const d = { left: fx, right: 1 - fx, top: fy, bottom: 1 - fy };
  return (Object.entries(d).sort((p, q) => p[1] - q[1])[0]?.[0] as Edge) ?? "center";
}

/** A layout an agent declares (`show_file(layout=…)`): the pane-tree shape with
 * workspace paths as leaves instead of group ids. Mirrors the backend's
 * `agent/shown_files.py` layout validation. */
export type LayoutLeaf = { type: "leaf"; path: string };
export type LayoutSplit = {
  type: "split";
  dir: "row" | "col";
  ratio: number;
  a: LayoutNode;
  b: LayoutNode;
};
export type LayoutNode = LayoutLeaf | LayoutSplit;

/** Leaf paths in visual order (left→right / top→bottom). */
export function layoutPaths(node: LayoutNode): string[] {
  return node.type === "leaf" ? [node.path] : [...layoutPaths(node.a), ...layoutPaths(node.b)];
}

/** The part of an editor group placement needs. Structural, so the shell's
 * `EditorGroup` fits without this module importing the hook. */
export type PlacedTab = { path: string; preview?: boolean; pinned?: boolean };
export type PlacedGroup = { id: string; tabs: PlacedTab[]; activePath: string | null };

/** Where a layout card opens (Q17), as a pure step over (tree, groups):
 *
 * - one pane: the card's layout replaces it and the pane's tabs join the
 *   layout's top-left leaf;
 * - already split: the whole existing tree moves left, the layout opens right.
 *
 * A card file already open anywhere is moved into its leaf, never duplicated;
 * a pane that move empties is dropped (an emptied SOLE pane leaves just the
 * layout). The shape decision reads the tree as it was, before the move. */
export function placeLayout(
  tree: PaneNode,
  groups: Record<string, PlacedGroup>,
  layout: LayoutNode,
  newId: () => string,
): { tree: PaneNode; groups: Record<string, PlacedGroup>; activeGroupId: string } {
  const cardPaths = new Set(layoutPaths(layout));
  const out: Record<string, PlacedGroup> = {};
  for (const [id, g] of Object.entries(groups)) {
    const tabs = g.tabs.filter((t) => !cardPaths.has(t.path));
    const activePath =
      g.activePath && cardPaths.has(g.activePath) ? (tabs[0]?.path ?? null) : g.activePath;
    out[id] = { ...g, tabs, activePath };
  }

  const cardLeafIds: string[] = [];
  const build = (node: LayoutNode): PaneNode => {
    if (node.type === "leaf") {
      const id = newId();
      out[id] = { id, tabs: [{ path: node.path }], activePath: node.path };
      cardLeafIds.push(id);
      return leaf(id);
    }
    const ratio = Math.max(MIN_RATIO, Math.min(MAX_RATIO, node.ratio));
    return { type: "split", dir: node.dir, ratio, a: build(node.a), b: build(node.b) };
  };
  const card = build(layout);
  const topLeft = cardLeafIds[0]!;

  if (tree.type === "leaf") {
    const old = out[tree.id];
    delete out[tree.id];
    if (old && old.tabs.length > 0) {
      const mine = out[topLeft]!;
      out[topLeft] = { ...mine, tabs: [...old.tabs, ...mine.tabs] };
    }
    return { tree: card, groups: out, activeGroupId: topLeft };
  }

  let left: PaneNode | null = tree;
  for (const id of leafIds(tree)) {
    if ((out[id]?.tabs.length ?? 0) > 0) continue;
    delete out[id];
    left = left && pruneLeaf(left, id);
  }
  return {
    tree: left ? { type: "split", dir: "row", ratio: 0.5, a: left, b: card } : card,
    groups: out,
    activeGroupId: topLeft,
  };
}

/** `removeLeaf`, except removing the sole leaf yields `null` (nothing left). */
function pruneLeaf(node: PaneNode, id: string): PaneNode | null {
  if (node.type === "leaf") return node.id === id ? null : node;
  return removeLeaf(node, id);
}
