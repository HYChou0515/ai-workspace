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

/**
 * Whether a copy came from the skill hub rather than the package — what the
 * listing says it is a copy OF (`copy_of`, from the folder's `.origin`). Not
 * derived from `source`: a copy of a package skill this App does not declare
 * has no row to shadow and lists as `workspace` + `is_copy` exactly like a
 * hub copy (review round 1 of #826). The panel words Update / Reset by it
 * (plan-skill-hub-ui-polish D4): "the shipped version" is the package's
 * phrase, and a hub copy updates to the version on the hub. An older API
 * that sends no `copy_of` reads as a package copy — what every copy read
 * before D4.
 */
export function hubCopy(skill: ItemSkillState): boolean {
  return skill.copy_of === "hub";
}
