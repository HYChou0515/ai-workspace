/**
 * Recursive editor-pane tree (VSCode editor groups). A node is either a
 * leaf (one file pane) or a split of two children along an axis. Edge
 * drops split the targeted leaf in place, so dropping on B's bottom edge
 * stacks B without disturbing a sibling A — true nesting.
 */

export type Edge = "left" | "right" | "top" | "bottom" | "center";

export type PaneLeaf = { type: "leaf"; id: string; path: string | null };
export type PaneSplit = { type: "split"; dir: "row" | "col"; a: PaneNode; b: PaneNode };
export type PaneNode = PaneLeaf | PaneSplit;

export function leaf(id: string, path: string | null): PaneLeaf {
  return { type: "leaf", id, path };
}

/** All leaves left-to-right / top-to-bottom. */
export function leaves(node: PaneNode): PaneLeaf[] {
  return node.type === "leaf" ? [node] : [...leaves(node.a), ...leaves(node.b)];
}

export function findLeaf(node: PaneNode, id: string): PaneLeaf | null {
  return leaves(node).find((l) => l.id === id) ?? null;
}

/** Split the leaf `id` along `edge`, placing a new leaf (newId/newPath) on
 * the edge side. "center" is a no-op here (callers open-in-place instead). */
export function splitLeaf(
  node: PaneNode,
  id: string,
  edge: Exclude<Edge, "center">,
  newId: string,
  newPath: string | null,
): PaneNode {
  if (node.type === "leaf") {
    if (node.id !== id) return node;
    const dir = edge === "left" || edge === "right" ? "row" : "col";
    const fresh = leaf(newId, newPath);
    const newFirst = edge === "left" || edge === "top";
    return { type: "split", dir, a: newFirst ? fresh : node, b: newFirst ? node : fresh };
  }
  return { ...node, a: splitLeaf(node.a, id, edge, newId, newPath), b: splitLeaf(node.b, id, edge, newId, newPath) };
}

export function setLeafPath(node: PaneNode, id: string, path: string | null): PaneNode {
  if (node.type === "leaf") return node.id === id ? { ...node, path } : node;
  return { ...node, a: setLeafPath(node.a, id, path), b: setLeafPath(node.b, id, path) };
}

/** Remove leaf `id`; its parent split collapses to the sibling. Returns
 * the new tree, or the unchanged root if it's the sole leaf. */
export function removeLeaf(node: PaneNode, id: string): PaneNode {
  if (node.type === "leaf") return node; // can't remove the root leaf
  if (node.a.type === "leaf" && node.a.id === id) return node.b;
  if (node.b.type === "leaf" && node.b.id === id) return node.a;
  return { ...node, a: removeLeaf(node.a, id), b: removeLeaf(node.b, id) };
}

/** Map a pointer position within a rect to a drop edge. The center 40%
 * box is "open here"; the outer margins pick a direction. */
export function edgeForPoint(
  x: number,
  y: number,
  rect: { left: number; top: number; width: number; height: number },
): Edge {
  const fx = (x - rect.left) / rect.width;
  const fy = (y - rect.top) / rect.height;
  if (fx >= 0.3 && fx <= 0.7 && fy >= 0.3 && fy <= 0.7) return "center";
  // distance to each edge; smallest wins
  const d = { left: fx, right: 1 - fx, top: fy, bottom: 1 - fy };
  return (Object.entries(d).sort((p, q) => p[1] - q[1])[0]?.[0] as Edge) ?? "center";
}
