import { useMemo } from "react";

import type { WorkflowManifestDTO } from "../api/workflows";
import { useWorkflowProfiles } from "./useWorkflow";
import { useWorkspaceWorkflows } from "./useWorkspaceWorkflows";

/**
 * The workflows an item can start: the profile's package workflows PLUS the ones
 * the user co-authored in THIS workspace (#323 `.workflows/`, offered for launch
 * by #400). A workspace def shadows a same-id package one — the run resolver does
 * the same. A file that will not parse is listed by the Workflows panel (with its
 * reason, no Run) and not offered here: picking it reached the pre-flight and came
 * back "no such workflow".
 *
 * Moved from the chat's New picker, which no longer starts workflows
 * (plan-workflow-out-of-chat D3 / D7): the Workflows sidebar does.
 */
export function useLaunchableWorkflows(
  slug: string,
  itemId: string,
  profile: string,
): WorkflowManifestDTO[] {
  const profilesQ = useWorkflowProfiles(slug);
  const wsWorkflowsQ = useWorkspaceWorkflows(slug, itemId);
  return useMemo<WorkflowManifestDTO[]>(() => {
    const byId = new Map<string, WorkflowManifestDTO>();
    for (const w of profilesQ.data?.find((p) => p.name === profile)?.workflows ?? [])
      byId.set(w.id, w);
    for (const w of wsWorkflowsQ.data ?? [])
      if (!w.problem) byId.set(w.id, { input_json: "", ...w });
    return [...byId.values()];
  }, [profilesQ.data, wsWorkflowsQ.data, profile]);
}
