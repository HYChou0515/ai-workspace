import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { CloseStatus } from "../api/types";

/**
 * Item write operations as TanStack mutations. Each invalidates the cache keys
 * its write affects, so the list and detail reads refetch automatically.
 */
export function useCloseInvestigation(slug: string, id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (status: CloseStatus | null) => api.closeInvestigation(slug, id, status),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.investigation(id) });
      void qc.invalidateQueries({ queryKey: qk.investigations });
    },
  });
}
