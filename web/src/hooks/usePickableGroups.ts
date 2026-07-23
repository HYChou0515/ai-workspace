import { useQuery } from "@tanstack/react-query";

import { type PickableGroup, groupsApi } from "../api/groups";
import { qk } from "../api/queryKeys";

/**
 * #608 — every group the signed-in user may grant to in a share dialog (name +
 * member count, never the member ids). Cached app-wide; degrades to `[]` if it
 * can't load, which just hides the group picker.
 */
export function usePickableGroups(): PickableGroup[] {
  const { data } = useQuery({
    queryKey: qk.pickableGroups,
    queryFn: () => groupsApi.listPickableGroups(),
  });
  return data ?? [];
}
