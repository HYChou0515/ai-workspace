import { useFileService } from "../api/fileService";
import { useItemProfile } from "../hooks/itemProfile";
import { useLaunchableWorkflows } from "../hooks/useLaunchableWorkflows";
import { useRun } from "../hooks/useWorkflow";
import { useWorkspaceSlug } from "../hooks/useWorkspaceSlug";
import { executionName, runIdOf } from "../lib/executionTab";

/** What an execution's tab, breadcrumb and chat card read (plan D10):
 * "Classify wafers · 10/12 14:03", from the same query the tab body polls.
 * Its own module so the chat's card does not import the whole execution view. */
export function ExecutionTitle({ path }: { path: string }) {
  const slug = useWorkspaceSlug();
  const itemId = useFileService().scopeId;
  const run = useRun(slug, itemId, runIdOf(path) ?? undefined).data;
  const workflows = useLaunchableWorkflows(slug, itemId, useItemProfile());
  const title = workflows.find((w) => w.id === run?.workflow_id)?.title || run?.workflow_id;
  return <>{title ? executionName(title, run?.started) : "Workflow"}</>;
}
