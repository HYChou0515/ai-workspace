import { createContext, useContext } from "react";

/**
 * The App slug of the current item workspace (#95). The workspace routes nest
 * under `/a/{slug}/items/{item_id}/...`, so every api call inside a workspace
 * needs the slug. Provided once by `WorkspaceShell` (from `manifest.slug`) and
 * read by the hooks/components instead of prop-drilling it everywhere.
 */
const WorkspaceSlugContext = createContext<string>("");

export const WorkspaceSlugProvider = WorkspaceSlugContext.Provider;

export function useWorkspaceSlug(): string {
  return useContext(WorkspaceSlugContext);
}
