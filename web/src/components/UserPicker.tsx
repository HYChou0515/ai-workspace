import { useState } from "react";

import { useUsers } from "../hooks/useUsers";
import { Icon } from "./Icon";
import { UserAvatar } from "./UserChip";

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
  const shown = users.filter(
    (u) =>
      !ex.has(u.id) &&
      (needle === "" || u.name.toLowerCase().includes(needle) || u.id.toLowerCase().includes(needle)),
  );

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
                <span style={{ color: "var(--text-paper-d2)", fontSize: 11 }}>{u.section}</span>
              </span>
              {sel.has(u.id) && <Icon name="check" size={14} color="var(--accent)" />}
            </button>
          </li>
        ))}
        {shown.length === 0 && (
          <li style={{ padding: "6px 8px", color: "var(--text-paper-d)", fontSize: 12 }}>
            No matches.
          </li>
        )}
      </ul>
    </div>
  );
}
