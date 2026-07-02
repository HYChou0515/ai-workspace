/**
 * Workspace skill file paths (#298 + #380). The skills picker reads its per-item
 * state via `api.getItemSkills` (the skill sibling of `getItemTools`); the only
 * bit that stays here is the download/import path helper, since a skill's files
 * ride the existing workspace file routes (a skill is just `.skill/<name>/…`).
 */

/** The workspace path a skill's files live under (`.skill/<name>/…`). The slug is
 * the folder name `save_skill` writes, so this is also the download prefix. */
export function skillDir(name: string): string {
  return `.skill/${name}`;
}
