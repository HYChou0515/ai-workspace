/**
 * Who may MANAGE an item — rewire its access, and decide how much of the
 * owner's quota its environment may spend.
 *
 * Deliberately not a sixth rung on the role ladder in `itemPermission.ts`. That
 * ladder is nested, so a new top rung would hand everyone "one degree past
 * Collaborator" the power to regrant the item to anybody, and would present it
 * in a dropdown as though it were more of the same thing. This is a different
 * KIND of grant and gets its own control and its own sentence.
 *
 * The backend has honoured this grant since #608 — `grantsAnySubject` over
 * `change_permission`, groups included. Nothing in the UI ever offered it, so in
 * practice only the owner and superusers held it: a live authorisation path with
 * no way to reach it, which is the dead-knob shape this codebase keeps
 * recording. It matters more now that the same verb decides who may spend the
 * owner's budget.
 */

import type { ItemPermission } from "./itemPermission";
import { userSubject } from "./permission";

const USER_PREFIX = "user:";
const asUser = (s: string): string | null => (s.startsWith(USER_PREFIX) ? s.slice(5) : null);

/**
 * The people explicitly granted management of `perm`, sorted.
 *
 * The owner is dropped: they hold it by BEING the owner (the backend bypasses
 * the grant list for them), so listing them would offer a revoke that does
 * nothing. Group subjects are ignored here and preserved on save — v1 grants
 * this to people only, and silently dropping a group an operator set by hand
 * would be worse than not offering to edit it.
 */
export function itemManagersFromPermission(perm: ItemPermission, owner: string): string[] {
  const subjects = perm.change_permission ?? [];
  const users = subjects.map(asUser).filter((u): u is string => u !== null && u !== owner);
  return [...new Set(users)].sort((a, b) => a.localeCompare(b));
}

/**
 * `perm` with its per-USER management grants replaced by `userIds`.
 *
 * Returns a new object — the dialog keeps the original to diff against, and
 * mutating it in place would make "did anything change" answer no.
 */
export function withItemManagers(perm: ItemPermission, userIds: string[]): ItemPermission {
  const kept = (perm.change_permission ?? []).filter((s) => asUser(s) === null);
  return { ...perm, change_permission: [...kept, ...userIds.map(userSubject)] };
}
