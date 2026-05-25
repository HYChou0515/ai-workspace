import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { CloseStatus, InvestigationInput } from "../api/types";

/**
 * Investigation write operations as TanStack mutations. Each invalidates the
 * cache keys its write affects, so the list and detail reads refetch
 * automatically instead of going stale behind the 30s window.
 */
export function useCreateInvestigation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: InvestigationInput) => api.createInvestigation(input),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.investigations }),
  });
}

export function useUpdateInvestigation(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: InvestigationInput) =>
      api.updateInvestigation(id, input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.investigation(id) });
      void qc.invalidateQueries({ queryKey: qk.investigations });
    },
  });
}

export function useCloseInvestigation(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (status: CloseStatus | null) =>
      api.closeInvestigation(id, status),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.investigation(id) });
      void qc.invalidateQueries({ queryKey: qk.investigations });
    },
  });
}

export function useAttachAgentConfig(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (configId: string | null) =>
      api.attachAgentConfig(id, configId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.investigation(id) }),
  });
}
