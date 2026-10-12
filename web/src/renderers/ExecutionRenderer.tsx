import { useFileService } from "../api/fileService";
import { ExecutionView } from "../components/ExecutionView";
import { useItemProfile } from "../hooks/itemProfile";
import { useOpenFile } from "../hooks/openFile";
import { useWorkspaceSlug } from "../hooks/useWorkspaceSlug";
import { runIdOf } from "../lib/executionTab";
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
