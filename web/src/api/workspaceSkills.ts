/**
 * Workspace skills API (#298) — the skills the user co-created with the agent in
 * an item's workspace, stored as `.skill/<name>/SKILL.md`. The IDE tree hides the
 * dot-folder, so the Skills panel is the surface for seeing / downloading / reusing
 * them. Download + import ride the existing workspace file routes (a skill is just
 * files), so there's no skill-specific transfer endpoint — only this listing.
 */

import { apiFetch } from "./http";

const enc = encodeURIComponent;

export type WorkspaceSkill = {
  name: string;
  description: string;
};

export const workspaceSkillsApi = {
  async list(slug: string, itemId: string): Promise<WorkspaceSkill[]> {
    const resp = await apiFetch(`/a/${enc(slug)}/items/${enc(itemId)}/skills`);
    if (!resp.ok) throw new Error(`list skills failed: ${resp.status}`);
    return resp.json();
  },
};

/** The workspace path a skill's files live under (`.skill/<name>/…`). The slug is
 * the folder name `save_skill` writes, so this is also the download prefix. */
export function skillDir(name: string): string {
  return `.skill/${name}`;
}
