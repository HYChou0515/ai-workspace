/**
 * Pure checkbox-selection semantics for FileTree's opt-in multi-select mode
 * (#415 card-gen picker). The selection set holds LEAF-FILE paths only; a
 * folder's checkbox is a derived tri-state and toggling a folder acts on every
 * leaf under it. Kept DOM-free so the branching logic is unit-testable.
 */

import type { TreeNode } from "./fileTree";

/** All leaf-file paths at or under `node` (the node itself when it's a file). */
export function leafPaths(node: TreeNode): string[] {
  return node.isDir ? node.children.flatMap(leafPaths) : [node.path];
}

export type TriState = "checked" | "indeterminate" | "unchecked";

/** A folder's checkbox state for the current selection: `checked` when every
 * leaf under it is selected, `unchecked` when none is, else `indeterminate`. A
 * folder with no leaves is `unchecked`. */
export function folderState(node: TreeNode, selected: ReadonlySet<string>): TriState {
  const leaves = leafPaths(node);
  const n = leaves.filter((p) => selected.has(p)).length;
  if (n === 0) return "unchecked";
  return n === leaves.length ? "checked" : "indeterminate";
}

/** Toggle a node's subtree in the selection: when every leaf under it is already
 * selected, remove them all; otherwise add them all. Works for a single leaf
 * too (its own path). Returns a NEW set (never mutates the input). */
export function toggleSubtree(node: TreeNode, selected: ReadonlySet<string>): Set<string> {
  const leaves = leafPaths(node);
  const next = new Set(selected);
  const allSelected = leaves.length > 0 && leaves.every((p) => next.has(p));
  for (const p of leaves) {
    if (allSelected) next.delete(p);
    else next.add(p);
  }
  return next;
}
