import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ActivityEntry, AgentConfigInfo } from "../api/types";

const STATIC = Number.POSITIVE_INFINITY;

/** Agent profiles for the picker. Near-static → cached indefinitely. */
export function useAgentConfigs(): AgentConfigInfo[] {
  const { data } = useQuery({
    queryKey: qk.agentConfigs,
    queryFn: () => api.listAgentConfigs(),
    staleTime: STATIC,
  });
  return data ?? [];
}

/** Investigation templates. Near-static → cached indefinitely. */
export function useTemplates(): string[] {
  const { data } = useQuery({
    queryKey: qk.templates,
    queryFn: () => api.listTemplates(),
    staleTime: STATIC,
  });
  return data ?? [];
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
