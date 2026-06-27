import { useQuery } from "@tanstack/react-query";

import { qk } from "../api/queryKeys";
import { workspaceSkillsApi } from "../api/workspaceSkills";

/** #298: the skills the user co-created in this workspace, for the Skills panel. */
export function useWorkspaceSkills(slug: string, itemId: string, enabled = true) {
  return useQuery({
    queryKey: qk.workspaceSkills(slug, itemId),
    queryFn: () => workspaceSkillsApi.list(slug, itemId),
    enabled,
  });
}
