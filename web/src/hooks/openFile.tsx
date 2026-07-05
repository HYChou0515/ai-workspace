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
