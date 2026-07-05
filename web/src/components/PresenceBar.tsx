/**
 * PresenceBar (#455 P4) — a live avatar stack of who ELSE has this item open, so
 * a viewer sees they're not editing alone (the collaboration cue that pairs with
 * live-sync). Reads the item's presence roster and drops the current user (you
 * know you're here); renders nothing when no one else is viewing, so the top bar
 * stays quiet when you're alone.
 */

import { useCurrentUser } from "../hooks/useCurrentUser";
import { useItemPresence } from "../hooks/useItemPresence";
import { pxToRem } from "../lib/pxToRem";
import { UserAvatar } from "./UserChip";

const MAX_SHOWN = 5;

export function PresenceBar({ slug, itemId }: { slug: string; itemId: string }) {
  const roster = useItemPresence(slug, itemId);
  const me = useCurrentUser();
  const others = roster.filter((u) => u !== me);
  if (others.length === 0) return null;

  const shown = others.slice(0, MAX_SHOWN);
  const overflow = others.length - shown.length;
  const label = `${others.length} other ${others.length === 1 ? "person" : "people"} viewing`;

  return (
    <div
      aria-label={label}
      title={label}
      style={{ display: "inline-flex", alignItems: "center", marginRight: 4 }}
    >
      {shown.map((u, i) => (
        <span key={u} style={{ marginLeft: i === 0 ? 0 : -6, display: "inline-flex" }}>
          <UserAvatar userId={u} size={22} />
        </span>
      ))}
      {overflow > 0 && (
        <span style={{ marginLeft: 4, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>+{overflow}</span>
      )}
    </div>
  );
}
