/**
 * Editor-groups state (VSCode model). A structural pane tree of leaves,
 * each leaf = a group with its own ordered tabs + active tab. One group is
 * "active" — the sidebar / palette open into it, and keyboard tab actions
 * target it. All mutations preserve the canonical VSCode behaviours
 * (preview tabs, pin, collapse-empty-group, cross-group move/copy).
 */

import { useCallback, useMemo, useRef, useState } from "react";

import {
  type Edge,
  type PaneNode,
  leaf,
  leafIds,
  removeLeaf,
  splitLeaf,
} from "../pages/investigation/paneTree";

export type EditorTab = {
  path: string;
  preview?: boolean; // single-click peek (italic); promoted on edit / double-click
  pinned?: boolean;
};

export type EditorGroup = {
  id: string;
  tabs: EditorTab[];
  activePath: string | null;
};

export type SplitDir = "left" | "right" | "up" | "down";

const dirToEdge = (d: SplitDir): Exclude<Edge, "center"> =>
  d === "up" ? "top" : d === "down" ? "bottom" : d;

export function useEditorGroups(initialPaths: string[]) {
  const seq = useRef(1);
  const [tree, setTree] = useState<PaneNode>(() => leaf("g0"));
  const [groups, setGroups] = useState<Record<string, EditorGroup>>(() => ({
    g0: {
      id: "g0",
      tabs: initialPaths.map((p) => ({ path: p })),
      activePath: initialPaths[0] ?? null,
    },
  }));
  const [activeGroupId, setActiveGroupId] = useState("g0");

  const activeGroup = groups[activeGroupId] ?? groups[leafIds(tree)[0]!]!;
  const activeFile = activeGroup?.activePath ?? null;

  const newGroupId = () => `g${seq.current++}`;

  const patchGroup = useCallback(
    (id: string, fn: (g: EditorGroup) => EditorGroup) =>
      setGroups((prev) => (prev[id] ? { ...prev, [id]: fn(prev[id]!) } : prev)),
    [],
  );

  /** Add/activate a tab in a group. preview replaces the prior preview tab. */
  const openInGroup = useCallback(
    (groupId: string, path: string, opts: { preview?: boolean } = {}) => {
      const preview = opts.preview ?? false;
      patchGroup(groupId, (g) => {
        const existing = g.tabs.find((t) => t.path === path);
        let tabs: EditorTab[];
        if (existing) {
          tabs =
            !preview && existing.preview
              ? g.tabs.map((t) => (t.path === path ? { ...t, preview: false } : t))
              : g.tabs;
        } else if (preview) {
          tabs = [...g.tabs.filter((t) => !t.preview || t.pinned), { path, preview: true }];
        } else {
          tabs = [...g.tabs, { path }];
        }
        return { ...g, tabs, activePath: path };
      });
      setActiveGroupId(groupId);
    },
    [patchGroup],
  );

  const openInActive = useCallback(
    (path: string, opts: { preview?: boolean } = {}) => openInGroup(activeGroupId, path, opts),
    [openInGroup, activeGroupId],
  );

  const selectTab = useCallback(
    (groupId: string, path: string) => {
      patchGroup(groupId, (g) => ({ ...g, activePath: path }));
      setActiveGroupId(groupId);
    },
    [patchGroup],
  );

  const focusGroup = useCallback((groupId: string) => setActiveGroupId(groupId), []);

  /** Remove a tab; if the group empties, collapse it (unless it's the last). */
  const closeTab = useCallback((groupId: string, path: string) => {
    setGroups((prevGroups) => {
      const g = prevGroups[groupId];
      if (!g) return prevGroups;
      const tabs = g.tabs.filter((t) => t.path !== path);
      if (tabs.length > 0) {
        const activePath =
          g.activePath === path ? (tabs[tabs.length - 1]?.path ?? null) : g.activePath;
        return { ...prevGroups, [groupId]: { ...g, tabs, activePath } };
      }
      // group emptied
      const ids = leafIds(tree);
      if (ids.length <= 1) {
        // keep the sole group, now empty
        return { ...prevGroups, [groupId]: { ...g, tabs: [], activePath: null } };
      }
      // collapse the leaf out of the tree + drop its group entry
      setTree((t) => removeLeaf(t, groupId));
      const rest = { ...prevGroups };
      delete rest[groupId];
      setActiveGroupId((cur) => (cur === groupId ? (leafIds(removeLeaf(tree, groupId))[0] ?? "g0") : cur));
      return rest;
    });
  }, [tree]);

  const reorderTab = useCallback(
    (groupId: string, from: number, to: number) =>
      patchGroup(groupId, (g) => {
        if (from === to || from < 0 || to < 0 || from >= g.tabs.length || to >= g.tabs.length) {
          return g;
        }
        const tabs = [...g.tabs];
        const [m] = tabs.splice(from, 1);
        if (m) tabs.splice(to, 0, m);
        return { ...g, tabs };
      }),
    [patchGroup],
  );

  const togglePin = useCallback(
    (groupId: string, path: string) =>
      patchGroup(groupId, (g) => ({
        ...g,
        tabs: g.tabs.map((t) =>
          t.path === path ? { ...t, pinned: !t.pinned, preview: false } : t,
        ),
      })),
    [patchGroup],
  );

  const closeOthers = useCallback(
    (groupId: string, keep: string) =>
      patchGroup(groupId, (g) => ({
        ...g,
        tabs: g.tabs.filter((t) => t.path === keep || t.pinned),
        activePath: keep,
      })),
    [patchGroup],
  );

  const closeToRight = useCallback(
    (groupId: string, from: string) =>
      patchGroup(groupId, (g) => {
        const idx = g.tabs.findIndex((t) => t.path === from);
        if (idx < 0) return g;
        return { ...g, tabs: g.tabs.filter((t, i) => i <= idx || t.pinned) };
      }),
    [patchGroup],
  );

  const closeGroupTabs = useCallback(
    (groupId: string) =>
      patchGroup(groupId, (g) => {
        const tabs = g.tabs.filter((t) => t.pinned);
        return { ...g, tabs, activePath: tabs[0]?.path ?? null };
      }),
    [patchGroup],
  );

  /** Split a group along edge, opening `path` in a fresh group on that side. */
  const splitGroup = useCallback((groupId: string, edge: Exclude<Edge, "center">, path: string | null) => {
    const id = newGroupId();
    setTree((t) => splitLeaf(t, groupId, edge, id));
    setGroups((prev) => ({
      ...prev,
      [id]: { id, tabs: path ? [{ path }] : [], activePath: path },
    }));
    setActiveGroupId(id);
  }, []);

  const splitActive = useCallback(
    (dir: SplitDir, path: string | null) => splitGroup(activeGroupId, dirToEdge(dir), path),
    [splitGroup, activeGroupId],
  );

  const collapseToSingle = useCallback(() => {
    setGroups((prev) => ({ [activeGroupId]: prev[activeGroupId]! }));
    setTree(leaf(activeGroupId));
  }, [activeGroupId]);

  /** Drag a tab onto another group: edge → split that group; center → open
   * in it. `copy` keeps the tab in the source group. */
  const dropTabOnGroup = useCallback(
    (fromGroup: string, toGroup: string, edge: Edge, path: string, copy: boolean) => {
      if (edge === "center") {
        openInGroup(toGroup, path, { preview: false });
        // open-in-place onto the SAME group is a no-op, so only vacate the
        // source when it's genuinely a different group.
        if (!copy && fromGroup && fromGroup !== toGroup) closeTab(fromGroup, path);
      } else {
        // an edge drop always lands in a brand-new group → vacate the
        // source unless the user held Ctrl/⌘ to copy (even when the drag
        // started from the very group being split).
        const id = newGroupId();
        setTree((t) => splitLeaf(t, toGroup, edge, id));
        setGroups((prev) => ({ ...prev, [id]: { id, tabs: [{ path }], activePath: path } }));
        setActiveGroupId(id);
        if (!copy && fromGroup) closeTab(fromGroup, path);
      }
    },
    [openInGroup, closeTab],
  );

  return useMemo(
    () => ({
      tree,
      groups,
      activeGroupId,
      activeGroup,
      activeFile,
      openInActive,
      openInGroup,
      selectTab,
      focusGroup,
      closeTab,
      reorderTab,
      togglePin,
      closeOthers,
      closeToRight,
      closeGroupTabs,
      splitGroup,
      splitActive,
      collapseToSingle,
      dropTabOnGroup,
      isSplit: leafIds(tree).length > 1,
    }),
    [
      tree,
      groups,
      activeGroupId,
      activeGroup,
      activeFile,
      openInActive,
      openInGroup,
      selectTab,
      focusGroup,
      closeTab,
      reorderTab,
      togglePin,
      closeOthers,
      closeToRight,
      closeGroupTabs,
      splitGroup,
      splitActive,
      collapseToSingle,
      dropTabOnGroup,
    ],
  );
}
