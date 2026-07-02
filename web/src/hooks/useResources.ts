import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ActivityEntry, AppItem, AppManifest, AppSummary } from "../api/types";

const STATIC = Number.POSITIVE_INFINITY;

/** #383: default the homepage item list to most-recently-updated first.
 * `updated_time` is specstar auto-tracked meta (sortable without an index);
 * without an explicit `sorts` the autocrud list returns creation order. */
const APP_ITEMS_SORTS = JSON.stringify([
  { type: "meta", key: "updated_time", direction: "-" },
]);

/** Platform Apps for the launcher (#89). Near-static → cached indefinitely. */
export function useApps(): AppSummary[] {
  const { data } = useQuery({
    queryKey: qk.apps,
    queryFn: () => api.listApps(),
    staleTime: STATIC,
  });
  return data ?? [];
}

/** One App's full manifest (#89 dashboard branding/layout/nouns). Near-static. */
export function useAppManifest(slug: string): AppManifest | undefined {
  const { data } = useQuery({
    queryKey: qk.appManifest(slug),
    queryFn: () => api.getAppManifest(slug),
    staleTime: STATIC,
  });
  return data;
}

/** An App's items, fetched from its `resource_route` (skipped until known).
 *
 * Exposes `isPending` (#225) so the dashboard can tell "still loading" apart
 * from "no items": an empty list before the first response would otherwise
 * flash the first-user "create your first" hero. `isPending` is true only when
 * there's no cached data yet, so a refetch (e.g. after creating an item) keeps
 * the list on screen instead of flickering back to a skeleton. */
export function useAppItems(
  slug: string,
  resourceRoute: string | undefined,
): { items: AppItem[]; isPending: boolean } {
  const { data, isPending } = useQuery({
    queryKey: qk.appItems(slug),
    queryFn: () => api.listAppItems(resourceRoute as string, { sorts: APP_ITEMS_SORTS }),
    enabled: !!resourceRoute,
  });
  return { items: data ?? [], isPending };
}

/** One App item by id (skipped until the App's resource_route is known). */
export function useAppItem(
  slug: string,
  resourceRoute: string | undefined,
  id: string,
): AppItem | undefined {
  const { data } = useQuery({
    queryKey: qk.appItem(slug, id),
    queryFn: () => api.getAppItem(resourceRoute as string, id),
    enabled: !!resourceRoute && !!id,
  });
  return data;
}

/** Inline-edit one of an App item's fields (#89 P7b). specstar CRUD has no
 * partial PATCH, so we PUT the whole item with the one field changed, then
 * invalidate the item + list caches. Returns a `setField(name, value)` the
 * DomainField select/text editors call. */
export function useUpdateItemField(slug: string, resourceRoute: string, item: AppItem) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: (patch: Record<string, unknown>) =>
      api.updateAppItem(resourceRoute, item.resource_id, { ...item, ...patch }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.appItem(slug, item.resource_id) });
      void qc.invalidateQueries({ queryKey: qk.appItems(slug) });
    },
  });
  return {
    /** Commit one field (breadcrumb/footer inline-edit). */
    setField: (name: string, value: unknown) => mutation.mutate({ [name]: value }),
    /** Commit several fields at once (the edit form). */
    setFields: (patch: Record<string, unknown>) => mutation.mutate(patch),
    isPending: mutation.isPending,
  };
}

/** Global activity feed; polls every 20s so the notifications badge stays
 *  fresh while the user lingers on Home. */
export function useActivity(): ActivityEntry[] {
  const { data } = useQuery({
    queryKey: qk.activity,
    queryFn: () => api.listActivity(),
    refetchInterval: 20_000,
  });
  return data ?? [];
}
