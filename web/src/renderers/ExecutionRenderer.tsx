import { useFileService } from "../api/fileService";
import { ExecutionView } from "../components/ExecutionView";
import { useLaunchableWorkflows } from "../hooks/useLaunchableWorkflows";
import { useRun } from "../hooks/useWorkflow";
import { useItemProfile } from "../hooks/itemProfile";
import { useOpenFile } from "../hooks/openFile";
import { useWorkspaceSlug } from "../hooks/useWorkspaceSlug";
import { executionName, runIdOf } from "../lib/executionTab";
import type { RendererProps } from "./registry";

/** An execution's tab body (plan-workflow-out-of-chat D7): the registry hands it
 * only the tab's path, so the item, its profile and the file opener come from
 * the workspace's context. */
export function ExecutionRenderer({ path }: RendererProps) {
  const slug = useWorkspaceSlug();
  const service = useFileService();
  const profile = useItemProfile();
  const openFile = useOpenFile();
  return (
    <ExecutionView
      slug={slug}
      itemId={service.scopeId}
      runId={runIdOf(path) ?? ""}
      profile={profile}
      fileService={service}
      onOpenFile={(p) => openFile?.(p)}
    />
  );
}

/** What an execution's tab and breadcrumb read (D10): "Classify wafers · 10/12
 * 14:03", the same query the tab body polls. */
export function ExecutionTitle({ path }: { path: string }) {
  const slug = useWorkspaceSlug();
  const itemId = useFileService().scopeId;
  const run = useRun(slug, itemId, runIdOf(path) ?? undefined).data;
  const workflows = useLaunchableWorkflows(slug, itemId, useItemProfile());
  const title = workflows.find((w) => w.id === run?.workflow_id)?.title || run?.workflow_id;
  return <>{title ? executionName(title, run?.started) : "Workflow"}</>;
}
