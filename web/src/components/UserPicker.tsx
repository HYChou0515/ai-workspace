import { useId, useMemo, useState } from "react";

import { useCurrentUser } from "../hooks/useCurrentUser";
import { useUsers } from "../hooks/useUsers";
import { readFrecency, recordPick, removePick } from "../lib/peopleFrecency";
import { useT } from "../lib/i18n";
import { rankPeople } from "../lib/rankPeople";
import { Icon } from "./Icon";
import { UserAvatar } from "./UserChip";
import { pxToRem } from "../lib/pxToRem";
import type { User } from "../api/types";

/** How many frequent people get the label (docs/plan-people-picker-ranking.md D6). */
const FREQUENT_SHOWN = 8;

/**
 * A filterable list of the company directory for picking people (share /
 * @mention / schedules / groups — every people picker in the app). The
 * directory is small, so it's fetched once and filtered on the client.
 * `selected` ids show a check; clicking a row calls `onToggle`.
 *
 * Ordered by `rankPeople` (docs/plan-people-picker-ranking.md): before anything
 * is typed — `related` people, then the ones this person picks often (in this
 * browser, `peopleFrecency`), then those whose section is nearest theirs, then
 * by name; typed text ranks by how well it matches first. The often-picked
 * part is read ONCE when the picker opens: picking someone records it for next
 * time but does not reorder the list under the pointer.
 */
export function UserPicker({
  selected,
  onToggle,
  exclude = [],
  placeholder = "Search people…",
  labelledBy,
  related,
}: {
  selected: string[];
  onToggle: (id: string) => void;
  exclude?: string[];
  placeholder?: string;
  /** id of the element naming this field, for callers that render their own
   * `<label>`. Without it such a label points at nothing: the search box lives
   * in here, so `htmlFor` on the outside cannot reach it. */
  labelledBy?: string;
  /** People related to where this picker is (D4) — listed first. */
  related?: string[];
}) {
  const t = useT();
  const frequentLabelId = useId();
  const users = useUsers();
  const me = useCurrentUser();
  // The viewer's own section, from the directory already in hand.
  const viewerSection = users.find((u) => u.id === me)?.section ?? "";
  const [q, setQ] = useState("");
  // Frequent people the viewer removed during THIS open (D6): gone from the
  // label now, as well as from storage for next time.
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set());
  // Read once per viewer — not on every pick, so the list holds still (D5).
  const picked = useMemo(() => readFrecency(me), [me]);
  const sel = new Set(selected);
  const ex = new Set(exclude);
  const candidates = users.filter((u) => !ex.has(u.id));
  const typing = q.trim() !== "";

  const frecency = useMemo(() => {
    const live = [...picked].filter(([id]) => !removed.has(id));
    if (typing) return new Map(live);
    // Before typing, only the top few count as frequent — the ones labelled.
    const visible = new Set(candidates.map((u) => u.id));
    return new Map(
      live
        .filter(([id, score]) => visible.has(id) && score > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, FREQUENT_SHOWN),
    );
  }, [picked, removed, typing, candidates]);

  // Search across every identity surface a person typing into the picker
  // might use: display name, stable id (what's persisted on records and
  // shown on chips), email (typing the local part before `@` is common
  // when the user is looking at someone's signature), and section (for
  // "everyone in Reflow").
  const shown = rankPeople(candidates, {
    query: q,
    viewerSection,
    related,
    frecency,
  });
  const relatedIds = new Set(related ?? []);
  // Contiguous by construction: related first, then frequent (D5).
  const lead = typing ? [] : shown.filter((u) => relatedIds.has(u.id));
  const frequent = typing ? [] : shown.filter((u) => !relatedIds.has(u.id) && frecency.has(u.id));
  const rest = shown.filter((u) => !lead.includes(u) && !frequent.includes(u));

  const pick = (id: string) => {
    if (!sel.has(id)) recordPick(me, id);
    onToggle(id);
  };
  const forget = (id: string) => {
    removePick(me, id);
    setRemoved((prev) => new Set(prev).add(id));
  };

  const row = (u: User, removable: boolean) => (
    <li key={u.id} style={{ display: "flex", alignItems: "center" }}>
      <button
        type="button"
        onClick={() => pick(u.id)}
        style={{
          flex: 1,
          minWidth: 0,
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
          <span data-person-name style={{ fontWeight: 500 }}>
            {u.name}
          </span>{" "}
          <span style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(11) }}>
            {u.id}
            {u.section ? ` · ${u.section}` : ""}
          </span>
        </span>
        {sel.has(u.id) && <Icon name="check" size={14} color="var(--accent)" />}
      </button>
      {removable && (
        <button
          type="button"
          className="icon-btn"
          aria-label={t("people.removeFrequent", { name: u.name })}
          title={t("people.removeFrequent", { name: u.name })}
          onClick={() => forget(u.id)}
          style={{ flex: "none", padding: "4px 6px", color: "var(--text-paper-d)" }}
        >
          <Icon name="x" size={12} />
        </button>
      )}
    </li>
  );

  return (
    <div style={{ minWidth: 240 }}>
      <input
        autoComplete="off"
        type="search"
        className="input"
        aria-labelledby={labelledBy}
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
      <ul className="scrollable" style={{ listStyle: "none", margin: 0, padding: 0, maxHeight: "min(240px, 22vh)", overflowY: "auto" }}>
        {lead.map((u) => row(u, false))}
        {frequent.length > 0 && (
          <li role="group" aria-labelledby={frequentLabelId}>
            <div
              id={frequentLabelId}
              style={{ padding: "4px 8px 2px", color: "var(--text-paper-d)", fontSize: pxToRem(11), fontWeight: 600 }}
            >
              {t("people.frequent")}
            </div>
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>{frequent.map((u) => row(u, true))}</ul>
          </li>
        )}
        {rest.map((u) => row(u, false))}
        {shown.length === 0 && (
          <li style={{ padding: "6px 8px", color: "var(--text-paper-d)", fontSize: pxToRem(12) }}>
            No matches.
          </li>
        )}
      </ul>
    </div>
  );
}
