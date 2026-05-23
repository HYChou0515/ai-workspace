/**
 * Per-file edit/preview mode for markdown. Lives above the editor so the
 * group tab strip can host the Edit/Preview toggle (VSCode-style, actions
 * next to the tabs) while the MarkdownRenderer reads the same state.
 * Keyed by path — toggling brief.md flips it wherever it's shown.
 */

import { createContext, useCallback, useContext, useState } from "react";

type EditModeApi = {
  isEditing: (path: string) => boolean;
  toggle: (path: string) => void;
};

const EditModeContext = createContext<EditModeApi | null>(null);

export function EditModeProvider({ children }: { children: React.ReactNode }) {
  const [editing, setEditing] = useState<Set<string>>(() => new Set());
  const isEditing = useCallback((path: string) => editing.has(path), [editing]);
  const toggle = useCallback(
    (path: string) =>
      setEditing((prev) => {
        const next = new Set(prev);
        if (next.has(path)) next.delete(path);
        else next.add(path);
        return next;
      }),
    [],
  );
  return (
    <EditModeContext.Provider value={{ isEditing, toggle }}>
      {children}
    </EditModeContext.Provider>
  );
}

export function useEditMode(): EditModeApi {
  // Tolerate use outside the provider (e.g. isolated tests) by no-op'ing.
  return useContext(EditModeContext) ?? { isEditing: () => false, toggle: () => {} };
}
