/**
 * Workflow templates API (#520) — the starter workflows the platform ships, and the one
 * action you take on them: pull a copy into this item.
 *
 * A copy, not a subscription. Once pulled, the item owns an ordinary
 * `.workflows/<id>.json` (see `workspaceWorkflows`) that the user edits freely; the
 * template is never read again. That is why the only mutation here is `copy` — there is
 * nothing to sync back.
 */

import { apiFetch } from "./http";
import type { WorkspacePhase } from "./workspaceWorkflows";

const enc = encodeURIComponent;

export type WorkflowTemplate = {
  id: string;
  title: string;
  description: string;
  tag: string;
  hint: string;
  phases: WorkspacePhase[];
  /** Whether THIS item's profile grants everything the template's steps need. */
  compatible: boolean;
  /** Why not, when `compatible` is false — shown so the user can fix the profile
   * instead of wondering why a card is greyed out. */
  problems: string[];
};

/** The item already has a workflow under this id. Distinguishable so the UI can offer
 * to replace rather than surfacing a raw error — the user's edited copy is on the line,
 * so replacing must be a deliberate second step. */
export class TemplateConflictError extends Error {}

async function detail(resp: Response, fallback: string): Promise<string> {
  try {
    const body = await resp.json();
    return typeof body?.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

export const workflowTemplatesApi = {
  async list(slug: string, itemId: string): Promise<WorkflowTemplate[]> {
    const resp = await apiFetch(
      `/a/${enc(slug)}/items/${enc(itemId)}/workflow-templates`,
    );
    if (!resp.ok) throw new Error(`list workflow templates failed: ${resp.status}`);
    return resp.json();
  },

  async copy(
    slug: string,
    itemId: string,
    name: string,
    opts: { overwrite?: boolean } = {},
  ): Promise<{ workflow_id: string; path: string }> {
    const qs = opts.overwrite ? "?overwrite=true" : "";
    const resp = await apiFetch(
      `/a/${enc(slug)}/items/${enc(itemId)}/workflow-templates/${enc(name)}/copy${qs}`,
      { method: "POST" },
    );
    if (resp.status === 409) {
      throw new TemplateConflictError(await detail(resp, "that name is already taken"));
    }
    if (!resp.ok) {
      throw new Error(await detail(resp, `copy template failed: ${resp.status}`));
    }
    return resp.json();
  },
};
