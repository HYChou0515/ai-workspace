import { useQuery } from "@tanstack/react-query";

import { qk } from "../api/queryKeys";
import { workflowTemplatesApi } from "../api/workflowTemplates";

/** #520: the starter workflows the platform ships, each flagged for whether THIS item's
 * profile can run it. Read by the Workflows panel's template section. */
export function useWorkflowTemplates(slug: string, itemId: string, enabled = true) {
  return useQuery({
    queryKey: qk.workflowTemplates(slug, itemId),
    queryFn: () => workflowTemplatesApi.list(slug, itemId),
    enabled,
  });
}
