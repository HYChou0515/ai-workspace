import type { ItemSkillState } from "../api/types";

/**
 * Whether a listed skill's files are IN this workspace — `.skill/<name>/`
 * exists here — as opposed to being the deploy's (a shared or profile skill
 * read from the package). True for a hand-written skill and a copy installed
 * from the skill hub (both `source: workspace`) and for a copy of a package
 * skill (`is_copy`, whose `source` stays the package's so a default-off skill
 * cannot be turned on for good by copying it).
 *
 * One predicate, two readers: the panel offers Download on exactly these, and
 * the hub picker marks an entry of that name as 「已有同名 skill」 — the same
 * set the install route refuses (`skill_folder_in_the_way`: the folder is
 * occupied), pinned by `tests/api/test_skill_hub_panel.py`.
 */
export function filesHere(skill: ItemSkillState): boolean {
  return skill.source === "workspace" || skill.is_copy === true;
}
