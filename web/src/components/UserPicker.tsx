import { useState } from "react";

import { useUsers } from "../hooks/useUsers";
import { Icon } from "./Icon";
import { UserAvatar } from "./UserChip";
import { pxToRem } from "../lib/pxToRem";

/**
 * A filterable list of the company directory for picking people (share /
 * @mention). The directory is small, so it's fetched once and filtered on the
 * client. `selected` ids show a check; clicking a row calls `onToggle`.
 */
export function UserPicker({
  selected,
  onToggle,
  exclude = [],
  placeholder = "Search people…",
}: {
  selected: string[];
  onToggle: (id: string) => void;
  exclude?: string[];
  placeholder?: string;
}) {
  const users = useUsers();
  const [q, setQ] = useState("");
  const sel = new Set(selected);
  const ex = new Set(exclude);
  const needle = q.trim().toLowerCase();
  // Search across every identity surface a person typing into the picker
  // might use: display name, stable id (what's persisted on records and
  // shown on chips), email (typing the local part before `@` is common
  // when the user is looking at someone's signature), and section (for
  // "everyone in Reflow"). Lazy `.toLowerCase()` is fine — directory has
  // a few hundred entries at most.
  const matches = (u: { id: string; name: string; section: string; email: string }) =>
    needle === "" ||
    u.name.toLowerCase().includes(needle) ||
    u.id.toLowerCase().includes(needle) ||
    u.email.toLowerCase().includes(needle) ||
    u.section.toLowerCase().includes(needle);
  const shown = users.filter((u) => !ex.has(u.id) && matches(u));

  return (
    <div style={{ minWidth: 240 }}>
      <input
        type="search"
        className="kb-input"
        placeholder={placeholder}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ width: "100%", marginBottom: 6 }}
      />
      <ul style={{ listStyle: "none", margin: 0, padding: 0, maxHeight: 240, overflowY: "auto" }}>
        {shown.map((u) => (
          <li key={u.id}>
            <button
              type="button"
              onClick={() => onToggle(u.id)}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 8px",
                textAlign: "left",
                color: "var(--text-paper)",
                borderRadius: "var(--radius-btn)",
              }}
            >
              <UserAvatar userId={u.id} size={22} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ fontWeight: 500 }}>{u.name}</span>{" "}
                <span style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(11) }}>
                  {u.id}
                  {u.section ? ` · ${u.section}` : ""}
                </span>
              </span>
              {sel.has(u.id) && <Icon name="check" size={14} color="var(--accent)" />}
            </button>
          </li>
        ))}
        {shown.length === 0 && (
          <li style={{ padding: "6px 8px", color: "var(--text-paper-d)", fontSize: pxToRem(12) }}>
            No matches.
          </li>
        )}
      </ul>
    </div>
  );
}
