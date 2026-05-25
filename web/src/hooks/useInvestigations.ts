import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { Investigation } from "../api/types";

type State =
  | { kind: "loading" }
  | { kind: "ready"; items: Investigation[] }
  | { kind: "error"; error: Error };

/**
 * The investigation list, cached under `qk.investigations`. Mutations
 * (create/update/close) invalidate this key so the list refreshes without a
 * manual refetch. `refresh()` forces a refetch on demand.
 */
export function useInvestigations(): State & { refresh: () => void } {
  const q = useQuery({
    queryKey: qk.investigations,
    queryFn: () => api.listInvestigations(),
  });
  const refresh = () => {
    void q.refetch();
  };
  if (q.isPending) return { kind: "loading", refresh };
  if (q.isError) return { kind: "error", error: q.error, refresh };
  return { kind: "ready", items: q.data, refresh };
}
