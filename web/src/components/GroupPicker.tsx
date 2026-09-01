import { useState } from "react";

import type { PickableGroup } from "../api/groups";
import { Icon } from "./Icon";
import { pxToRem } from "../lib/pxToRem";

/**
 * Pick a group to grant to — the same shape as `UserPicker`, for the same
 * reason: the list comes back in store order and a `<select>` cannot be typed
 * into, so past a handful of groups the only way to find yours was to read every
 * option. Sorted by name and filterable here; the list is org-sized, so it is
 * fetched once and narrowed on the client.
 */
export function GroupPicker({
  groups,
  onPick,
  exclude = [],
  placeholder = "Search groups…",
  labelledBy,
}: {
  groups: PickableGroup[];
  onPick: (groupId: string) => void;
  /** Ids already granted — offering them again would be a no-op. */
  exclude?: string[];
  placeholder?: string;
  /** id of the element naming this field, for callers rendering their own label:
   * the search box lives in here, so an outside `htmlFor` cannot reach it. */
  labelledBy?: string;
}) {
  const [q, setQ] = useState("");
  const ex = new Set(exclude);
  const needle = q.trim().toLowerCase();
  // Name and description both: the name is how a group is addressed, but people
  // reach for what it is FOR ("inspection") as often as what it is called.
  const matches = (g: PickableGroup) =>
    needle === "" ||
    g.name.toLowerCase().includes(needle) ||
    g.description.toLowerCase().includes(needle);
  const shown = groups
    .filter((g) => !ex.has(g.resource_id) && matches(g))
    // `localeCompare` rather than `<`: a plain comparison orders by code point, so
    // every lowercase name lands after every uppercase one, which reads as unsorted.
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));

  return (
    <div style={{ minWidth: 240 }}>
      <input
        type="search"
        className="kb-input"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : placeholder}
        placeholder={placeholder}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ width: "100%", marginBottom: 6 }}
      />
      {/* The list is what makes this component tall, and a share dialog stacks
          two pickers and two grant lists in one panel. 240px is fine on a tall
          screen and most of the budget on a laptop, so cap it against the
          viewport as well — the search box above stays outside this scroll
          area, so it can never be scrolled out of reach. */}
      <ul style={{ listStyle: "none", margin: 0, padding: 0, maxHeight: "min(240px, 22vh)", overflowY: "auto" }}>
        {shown.map((g) => (
          <li key={g.resource_id}>
            <button
              type="button"
              data-testid={`group-picker-item-${g.resource_id}`}
              data-group-name={g.name}
              onClick={() => onPick(g.resource_id)}
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
              <Icon name="users" size={16} color="var(--text-paper-d)" />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ fontWeight: 500 }}>{g.name}</span>{" "}
                <span style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(11) }}>
                  {g.member_count}
                  {g.description ? ` · ${g.description}` : ""}
                </span>
              </span>
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
