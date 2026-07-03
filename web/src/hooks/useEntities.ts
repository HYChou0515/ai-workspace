/**
 * TanStack Query hooks over the entity framework (#419). Reads are `useQuery`
 * (catalog + one type's records); the create / update write path is `useMutation`
 * that invalidates the type's record list on success (the same single write path
 * the agent + workflows use — the UI never edits frontmatter directly).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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

/** Create + update, both scoped to one entity type. Each invalidates that type's
 * record list so the open view refetches the freshly-projected records. */
export function useEntityMutations(slug: string, itemId: string, type: string) {
  const qc = useQueryClient();
  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: qk.entities.list(slug, itemId, type) });

  const create = useMutation<EntityInstance, Error, Record<string, unknown>>({
    mutationFn: (args) => entitiesApi.create(slug, itemId, type, args),
    onSuccess: invalidate,
  });

  const update = useMutation<
    EntityInstance,
    Error,
    { number: number; patch: Record<string, unknown> }
  >({
    mutationFn: ({ number, patch }) => entitiesApi.update(slug, itemId, type, number, patch),
    onSuccess: invalidate,
  });

  return {
    create: (args: Record<string, unknown>) => create.mutate(args),
    createAsync: (args: Record<string, unknown>) => create.mutateAsync(args),
    patch: (number: number, patch: Record<string, unknown>) => update.mutate({ number, patch }),
    isCreating: create.isPending,
    isPatching: update.isPending,
  };
}
