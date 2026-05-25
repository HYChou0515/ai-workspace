import { useUser } from "../hooks/useUsers";

/** A small round avatar (photo or initials) for a user id. */
export function UserAvatar({ userId, size = 28 }: { userId: string; size?: number }) {
  const u = useUser(userId);
  const initials =
    u.name
      .split(/[\s_-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((s) => s[0]?.toUpperCase() ?? "")
      .join("") || "?";
  return (
    <span
      title={u.name}
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: u.photo_url ? `center/cover url(${u.photo_url})` : "var(--paper-2)",
        color: "var(--text-paper)",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontWeight: 600,
        fontSize: Math.round(size * 0.43),
        border: "1px solid var(--paper-3)",
        flexShrink: 0,
      }}
    >
      {u.photo_url ? "" : initials}
    </span>
  );
}

/**
 * Renders a user id as their directory name (+ avatar). Used everywhere we'd
 * otherwise show a bare id (owner, members, author, notification actor…).
 */
export function UserChip({
  userId,
  size = 22,
  nameOnly = false,
}: {
  userId: string;
  size?: number;
  nameOnly?: boolean;
}) {
  const u = useUser(userId);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0 }}>
      {!nameOnly && <UserAvatar userId={userId} size={size} />}
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {u.name}
      </span>
    </span>
  );
}
