/**
 * Workspace workflows API (#323) — the workflows the user co-created with the agent in
 * an item's workspace, stored as `.workflows/<id>.json`. A workflow is DATA the platform
 * interprets, not code, so it's safe to run. The IDE tree hides the dot-folder, so the
 * Workflows panel is the surface for running / downloading / reusing them. Download +
 * import ride the existing workspace file routes (a workflow is just a file), so there's
 * no workflow-specific transfer endpoint — only this listing.
 */

import { apiFetch } from "./http";

const enc = encodeURIComponent;

export type WorkspacePhase = { id: string; title: string };

export type WorkspaceWorkflow = {
  id: string;
  title: string;
  phases: WorkspacePhase[];
};

export const workspaceWorkflowsApi = {
  async list(slug: string, itemId: string): Promise<WorkspaceWorkflow[]> {
    const resp = await apiFetch(`/a/${enc(slug)}/items/${enc(itemId)}/workflows`);
    if (!resp.ok) throw new Error(`list workflows failed: ${resp.status}`);
    return resp.json();
  },
};

/** The workspace folder a co-created workflow's file lives in (`.workflows/<id>.json`).
 * `save_workflow` writes here, so this is also the download prefix. */
export const WORKFLOWS_DIR = ".workflows";
