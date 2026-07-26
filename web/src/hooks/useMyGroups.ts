import { useQuery } from "@tanstack/react-query";

import { type Group, groupsApi } from "../api/groups";
import { qk } from "../api/queryKeys";

/**
 * #608 — the logical groups the signed-in user can see (owns / maintains /
 * belongs to), WITH member ids. `usePickableGroups` is the world-readable
 * name+count list for the share picker; this is the richer, permissioned list
 * used to expand a group grant on an item into WHO is in it. Degrades to `[]`
 * when it can't load. `enabled` gates the fetch so a panel with no group grants
 * never asks.
 */
export function useMyGroups(enabled = true): Group[] {
  const { data } = useQuery({
    queryKey: qk.groups,
    queryFn: () => groupsApi.listGroups(),
    enabled,
  });
  return data ?? [];
}
