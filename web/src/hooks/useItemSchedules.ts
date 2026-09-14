import { useQuery } from "@tanstack/react-query";

import { qk } from "../api/queryKeys";
import { schedulesApi } from "../api/schedules";

/** The item's schedules, interpreted by the backend, for the Workflows panel. */
export function useItemSchedules(slug: string, itemId: string, enabled = true) {
  return useQuery({
    queryKey: qk.itemSchedules(slug, itemId),
    queryFn: () => schedulesApi.list(slug, itemId),
    enabled,
  });
}
