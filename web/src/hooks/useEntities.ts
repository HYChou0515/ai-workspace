/**
 * TanStack Query read hooks over the entity framework (#419): the catalog + one
 * type's records + project health. The create / update write path lives in
 * `useEntityWrite` (#448 P2) — the single seam that carries the optimistic-lock
 * + conflict contract every renderer rides.
 */

import { useQuery } from "@tanstack/react-query";

import { type EntityCatalog, type EntityHealth, type EntityList, entitiesApi } from "../api/entities";
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
