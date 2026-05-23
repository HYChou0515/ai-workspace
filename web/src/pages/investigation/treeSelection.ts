/**
 * File-tree multi-selection semantics (VSCode Explorer). Pure functions so
 * the click logic is unit-testable independent of the DOM.
 */

import type { TreeNode } from "./fileTree";

export type SelMods = { ctrl: boolean; shift: boolean };
export type SelState = { selected: string[]; anchor: string | null };

/** Flatten the visible rows (depth-first, skipping collapsed folders'
 * children) into display order — the basis for shift-range selection. */
export function visibleOrder(tree: TreeNode[], isCollapsed: (path: string) => boolean): string[] {
  const out: string[] = [];
  const walk = (nodes: TreeNode[]): void => {
    for (const n of nodes) {
      out.push(n.path);
      if (n.isDir && !isCollapsed(n.path)) walk(n.children);
    }
  };
  walk(tree);
  return out;
}

/** Next selection after clicking `path` with the given modifiers, where
 * `order` is the visible-row order.
 *
 * - plain          → select only this (anchor here)
 * - ctrl/⌘         → toggle this; anchor here
 * - shift          → range anchor…this replaces the selection; anchor stays
 * - ctrl+shift     → range anchor…this ADDED to the selection; anchor stays
 */
export function nextSelection(
  state: SelState,
  path: string,
  mods: SelMods,
  order: string[],
): SelState {
  if (mods.shift && state.anchor) {
    const a = order.indexOf(state.anchor);
    const b = order.indexOf(path);
    if (a !== -1 && b !== -1) {
      const [lo, hi] = a <= b ? [a, b] : [b, a];
      const range = order.slice(lo, hi + 1);
      if (mods.ctrl) {
        const set = new Set(state.selected);
        for (const p of range) set.add(p);
        return { selected: [...set], anchor: state.anchor };
      }
      return { selected: range, anchor: state.anchor };
    }
  }
  if (mods.ctrl) {
    const set = new Set(state.selected);
    if (set.has(path)) set.delete(path);
    else set.add(path);
    return { selected: [...set], anchor: path };
  }
  return { selected: [path], anchor: path };
}
