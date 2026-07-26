/**
 * openFile context (#448 F / #454) — the workspace's "open this path in the IDE"
 * seam, exposed to deeply-nested renderers that the registry only hands `{ path }`
 * (so they can't receive an opener by prop). The `WorkspaceShell` publishes its
 * own `openFile` here; a renderer reads it with `useOpenFile()` to wire jump
 * affordances (e.g. the health view's click-to-fix).
 *
 * It is `null` outside a shell (a standalone preview / test), so callers gate the
 * jump UI on its presence rather than rendering a dead control.
 */

import { createContext, useContext, type ReactNode } from "react";

export type OpenFile = (path: string, opts?: { preview?: boolean }) => void;

const OpenFileContext = createContext<OpenFile | null>(null);

export function OpenFileProvider({ value, children }: { value: OpenFile; children: ReactNode }) {
  return <OpenFileContext.Provider value={value}>{children}</OpenFileContext.Provider>;
}

/** The workspace file opener, or `null` when rendered outside a `WorkspaceShell`. */
export function useOpenFile(): OpenFile | null {
  return useContext(OpenFileContext);
}

// Whether that opener leads anywhere the user can SEE. Collapsing the workspace
// unmounts it, so `openFile` still succeeds while the pane is folded — the tab is
// opened, just off screen, which reads as the click doing nothing. A caller that
// can be reached while folded (the chat's shown-file card) checks this and offers
// the file another way instead.
const WorkspaceVisibleContext = createContext(false);

export function WorkspaceVisibleProvider({
  value,
  children,
}: {
  value: boolean;
  children: ReactNode;
}) {
  return <WorkspaceVisibleContext.Provider value={value}>{children}</WorkspaceVisibleContext.Provider>;
}

/** True when the workspace's file pane is on screen. `false` by default: a
 * surface that never published it has no pane to open into. */
export function useWorkspaceVisible(): boolean {
  return useContext(WorkspaceVisibleContext);
}
