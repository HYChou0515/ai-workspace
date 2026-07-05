/**
 * TanStack Query read hooks over the entity framework (#419): the catalog + one
 * type's records + project health. The create / update write path lives in
 * `useEntityWrite` (#448 P2) — the single seam that carries the optimistic-lock
 * + conflict contract every renderer rides.
 */

import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  type EntityCatalog,
  type EntityHealth,
  type EntityInstance,
  type EntityList,
  entitiesApi,
} from "../api/entities";
import { qk } from "../api/queryKeys";

export function useEntityCatalog(slug: string, itemId: string) {
  return useQuery<EntityCatalog>({
    queryKey: qk.entities.catalog(slug, itemId),
    queryFn: () => entitiesApi.catalog(slug, itemId),
    enabled: !!slug && !!itemId,
  });
}

export function useEntityHealth(slug: string, itemId: string, enabled: boolean) {
  return useQuery<EntityHealth>({
    queryKey: qk.entities.health(slug, itemId),
    queryFn: () => entitiesApi.health(slug, itemId),
    enabled: enabled && !!slug && !!itemId,
  });
}

export function useEntities(slug: string, itemId: string, type: string) {
  return useQuery<EntityList>({
    queryKey: qk.entities.list(slug, itemId, type),
    queryFn: () => entitiesApi.list(slug, itemId, type),
    enabled: !!slug && !!itemId && !!type,
  });
}

/** Load the record lists of several referenced types at once (#448 P4) so the
 * renderer can resolve ref-traversal columns + populate ref pickers. `useQueries`
 * takes a dynamic list, so the set can vary with the open view's schema. */
export function useReferencedRecords(
  slug: string,
  itemId: string,
  types: string[],
): Record<string, EntityInstance[]> {
  const results = useQueries({
    queries: types.map((t) => ({
      queryKey: qk.entities.list(slug, itemId, t),
      queryFn: () => entitiesApi.list(slug, itemId, t),
      enabled: !!slug && !!itemId && !!t,
    })),
  });
  // Re-derive only when a referenced list actually refetches (dataUpdatedAt),
  // not on every render (`useQueries` returns a fresh array each time).
  const stamp = results.map((r) => r.dataUpdatedAt).join(",");
  const byType: Record<string, EntityInstance[]> = {};
  types.forEach((t, i) => {
    byType[t] = results[i]?.data?.entities ?? [];
  });
  return useMemo(() => byType, [types, stamp]);
}
