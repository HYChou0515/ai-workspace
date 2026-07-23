/**
 * #608 — the caller's capabilities on a group, mirroring the backend gates
 * (`group_routes._require_owner` / `_require_manager`): the owner (or a superuser)
 * manages members + maintainers + transfer + delete; a maintainer manages MEMBERS
 * only. Keep this pure so the /groups page and its tests agree with the server.
 */

import type { Group } from "../api/groups";

export type GroupCapabilities = {
  /** Add / remove members (owner, maintainer, or superuser). */
  canManageMembers: boolean;
  /** Add / remove maintainers, transfer ownership, delete (owner or superuser). */
  canManageGroup: boolean;
};

export function groupCapabilities(
  group: Group,
  currentUserId: string,
  isSuperuser: boolean,
): GroupCapabilities {
  const canManageGroup = isSuperuser || group.owner === currentUserId;
  const canManageMembers = canManageGroup || group.maintainers.includes(currentUserId);
  return { canManageMembers, canManageGroup };
}

/** Display label for the caller's relationship to a group. */
export function groupRoleLabel(group: Group, currentUserId: string, isSuperuser: boolean): string {
  if (group.owner === currentUserId) return "Owner";
  if (group.maintainers.includes(currentUserId)) return "Maintainer";
  if (group.members.includes(currentUserId)) return "Member";
  return isSuperuser ? "Admin" : "";
}
