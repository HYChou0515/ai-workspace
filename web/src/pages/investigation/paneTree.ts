/**
 * Recursive editor-group tree (VSCode editor groups). Nodes are purely
 * structural — a leaf carries only a group `id`; the group's tabs/active
 * file live in a separate `Map<id, Group>` owned by the shell. Edge drops
 * split the targeted leaf in place, so splitting one group never disturbs
 * a sibling (true nesting).
 */

export type Edge = "left" | "right" | "top" | "bottom" | "center";

export type PaneLeaf = { type: "leaf"; id: string };
export type PaneSplit = { type: "split"; dir: "row" | "col"; a: PaneNode; b: PaneNode };
export type PaneNode = PaneLeaf | PaneSplit;

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
    return { type: "split", dir, a: newFirst ? fresh : node, b: newFirst ? node : fresh };
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
