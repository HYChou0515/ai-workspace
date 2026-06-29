import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useMemo } from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ApiClient, ToolCatalogEntry } from "../api/types";

/**
 * Tool display catalog (#322) — the backend's `name → {label, description}` map
 * (GET /tools), made available to the chat tool cards (`AgentEntryView`) so an
 * unmapped tool (e.g. a package command the FE i18n map never listed) shows a
 * clean label instead of leaking its raw `snake_case` name.
 *
 * Exposed as a context with an EMPTY default so the low-level entry renderer
 * needs no QueryClient (and existing tests render it bare): production wraps the
 * chat surfaces in `<ToolCatalogProvider>`, which fetches once and fills the map.
 * The FE i18n `TOOL_LABEL` still overlays nicer localized labels on top of this
 * for curated tools — the catalog is only the guaranteed fallback.
 */
export const ToolCatalogContext = createContext<Map<string, ToolCatalogEntry>>(new Map());

export function ToolCatalogProvider({
  children,
  client = api,
}: {
  children: React.ReactNode;
  client?: Pick<ApiClient, "getToolsCatalog">;
}) {
  const q = useQuery({
    queryKey: qk.toolsCatalog,
    queryFn: () => client.getToolsCatalog(),
    staleTime: Number.POSITIVE_INFINITY, // the tool set is static for a deploy
  });
  const map = useMemo(
    () => new Map((q.data ?? []).map((e) => [e.name, e] as const)),
    [q.data],
  );
  return <ToolCatalogContext.Provider value={map}>{children}</ToolCatalogContext.Provider>;
}

/** Resolve a tool's clean display label from the backend catalog — `undefined`
 * when the catalog hasn't loaded or doesn't know the tool (caller falls back). */
export function useToolLabel(): (name: string) => string | undefined {
  const map = useContext(ToolCatalogContext);
  return (name: string) => map.get(name)?.label;
}
