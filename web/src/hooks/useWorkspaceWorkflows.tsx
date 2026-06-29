import { useQuery } from "@tanstack/react-query";

import { qk } from "../api/queryKeys";
import { workspaceWorkflowsApi } from "../api/workspaceWorkflows";

/** #323: the workflows the user co-created in this workspace, for the Workflows panel. */
export function useWorkspaceWorkflows(slug: string, itemId: string, enabled = true) {
  return useQuery({
    queryKey: qk.workspaceWorkflows(slug, itemId),
    queryFn: () => workspaceWorkflowsApi.list(slug, itemId),
    enabled,
  });
}
