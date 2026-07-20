import { useState } from "react";

import type { AppItem, AppManifest } from "../api/types";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { useIsSuperuser } from "../hooks/useIsSuperuser";
import { useSetItemPermission } from "../hooks/useResources";
import {
  type ItemPermission,
  canChangeItemPermission,
  itemGrantsFromPermission,
  itemRoleDef,
  parseItemPermission,
} from "../lib/itemPermission";
import { pxToRem } from "../lib/pxToRem";
import { ItemShareDialog } from "./ItemShareDialog";
import { UserChip } from "./UserChip";

/**
 * #306 PR3 — the one roster-and-access panel for a work item, shared by the top
 * bar's people popover and the left sidebar.
 *
 * Those two were separate read-only lists of the same `item.members` array under
 * two different words ("Members" / "Reviewers"), and neither said anything about
 * ACCESS — so the roster and the permission grants could disagree with nothing on
 * screen admitting it. One component, one word (the App's `members` field label),
 * and each row carries the role its grants actually add up to.
 *
 * Editing goes through {@link ItemShareDialog} rather than a second inline editor:
 * the grant→role ladder already lives there, and `PUT …/items/{id}/permission` is
 * the only endpoint allowed to write `permission`.
 */
export function ItemMembersPanel({
  manifest,
  item,
  variant = "sidebar",
}: {
  manifest: AppManifest;
  item: AppItem;
  /** `popover` drops the heading chrome for the top bar's dropdown. */
  variant?: "sidebar" | "popover";
}) {
  const me = useCurrentUser();
  const isSuperuser = useIsSuperuser();
  const [sharing, setSharing] = useState(false);
  const owner = (item.created_by as string) || (item.owner as string) || "";
  const perm = parseItemPermission((item as Record<string, unknown>).permission);
  const access = useSetItemPermission(manifest.slug, item.resource_id);
  const canManage = canChangeItemPermission(perm, me, owner, isSuperuser);
  const label = manifest.labels?.members ?? "Members";

  return (
    <div style={variant === "sidebar" ? sidebarBody : popoverBody}>
      <div style={titleRow}>
        <span data-testid="members-title" className="caps">
          {label}
        </span>
        {canManage && (
          <button
            type="button"
            data-testid="members-manage"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            onClick={() => setSharing(true)}
          >
            Manage access…
          </button>
        )}
      </div>

      <ul style={list}>
        {rosterOf(item, perm, owner).map((row) => (
          <li key={row.userId} data-testid={`member-row-${row.userId}`} style={rowStyle}>
            <UserChip userId={row.userId} />
            <span style={roleText}>{row.role}</span>
          </li>
        ))}
      </ul>

      {sharing && (
        <ItemShareDialog
          itemName={(item.title as string) || manifest.item.noun}
          owner={owner}
          value={perm ?? { visibility: "private" }}
          busy={access.isPending}
          error={access.error}
          onSubmit={(next) => {
            void access.setPermissionAsync(next).then(
              () => setSharing(false),
              () => {},
            );
          }}
          onClose={() => setSharing(false)}
        />
      )}
    </div>
  );
}

type RosterRow = { userId: string; role: string };

/**
 * Owner first, then everyone the item touches — the declared roster (`members`)
 * UNIONED with whoever actually holds a grant, so neither an unreachable roster
 * entry nor an off-roster grantee can hide.
 *
 * The role is what the grants add up to, read through the same ladder the share
 * dialog writes. `No access` is not padding: a member with no grants is a real
 * drift state (the roster is a plain field; grants are separate), and it used to
 * be completely invisible.
 */
function rosterOf(item: AppItem, perm: ItemPermission | undefined, owner: string): RosterRow[] {
  const roleByUser = new Map(
    perm ? itemGrantsFromPermission(perm, owner).map((g) => [g.userId, g]) : [],
  );
  const members = ((item.members as string[] | undefined) ?? []).filter((m) => m !== owner);
  const others = [...new Set([...members, ...roleByUser.keys()])].filter((u) => u !== owner);

  return [
    { userId: owner, role: "Owner" },
    ...others.map((userId) => {
      const grant = roleByUser.get(userId);
      if (!grant) return { userId, role: "No access" };
      // A Custom (non-nested) grant keeps its raw verbs; name it for what it is
      // rather than rounding it to the nearest ladder rung.
      return { userId, role: grant.verbs.size > 0 ? "Custom" : itemRoleDef(grant.role).label };
    }),
  ];
}

const sidebarBody: React.CSSProperties = { padding: 12, display: "grid", gap: 10 };
const popoverBody: React.CSSProperties = { minWidth: 240, padding: "10px 12px", display: "grid", gap: 8 };
const titleRow: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
};
const list: React.CSSProperties = { listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 };
const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  fontSize: pxToRem(12),
};
const roleText: React.CSSProperties = { color: "var(--text-paper-d)", fontSize: pxToRem(11) };
