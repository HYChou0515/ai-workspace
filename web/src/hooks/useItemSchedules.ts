import { useQuery } from "@tanstack/react-query";

import { qk } from "../api/queryKeys";
import { schedulesApi } from "../api/schedules";

/** Every schedule of the item — its own and each page's — for its Workflows
 * panel: the schedules overview narrowed to this item, the same rows the
 * overview lists (`docs/plan-schedule-run-as.md` G10). */
export function useItemSchedules(slug: string, itemId: string, enabled = true) {
  return useQuery({
    queryKey: qk.itemSchedules(slug, itemId),
    queryFn: () => schedulesApi.itemOverview(slug, itemId),
    enabled,
  });
}
