import { useId, useMemo, useRef, useState } from "react";

import { useCurrentUserState } from "../hooks/useCurrentUser";
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
  placeholder,
  labelledBy,
  related,
  rememberPicks = true,
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
  /** Count a pick toward this person's "frequent" list (D2). Off for one-off
   * picks — handing a skill or a group to its next owner, an admin's per-person
   * exception — which would otherwise surface that person in every share and
   * @-mention picker for weeks. */
  rememberPicks?: boolean;
}) {
  const t = useT();
  const labelIds = { related: useId(), frequent: useId(), all: useId() };
  const users = useUsers();
  // Until `/me` answers, `id` is a placeholder: reading or recording under it
  // would mix one shared list into everyone's (review round 1).
  const { id: me, ready } = useCurrentUserState();
  const searchRef = useRef<HTMLInputElement>(null);
  // The viewer's own section, from the directory already in hand.
  const viewerSection = users.find((u) => u.id === me)?.section ?? "";
  const [q, setQ] = useState("");
  // Frequent people the viewer removed during THIS open (D6): gone from the
  // label now, as well as from storage for next time.
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set());
  // Read once per viewer — not on every pick, so the list holds still (D5).
  const picked = useMemo(() => (ready ? readFrecency(me) : new Map<string, number>()), [me, ready]);
  const sel = new Set(selected);
  const ex = new Set(exclude);
  const candidates = users.filter((u) => !ex.has(u.id));
  const typing = q.trim() !== "";

  const relatedIds = new Set(related ?? []);
  const frecency = useMemo(() => {
    const live = [...picked].filter(([id]) => !removed.has(id));
    if (typing) return new Map(live);
    // Before typing, only the top few count as frequent — the ones labelled.
    // Related people are listed above them anyway, so they take no slot.
    const visible = new Set(candidates.map((u) => u.id));
    const lead = new Set(related ?? []);
    return new Map(
      live
        .filter(([id, score]) => visible.has(id) && !lead.has(id) && score > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, FREQUENT_SHOWN),
    );
  }, [picked, removed, typing, candidates, related]);

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
  // Contiguous by construction: related first, then frequent (D5).
  const lead = typing ? [] : shown.filter((u) => relatedIds.has(u.id));
  const frequent = typing ? [] : shown.filter((u) => !relatedIds.has(u.id) && frecency.has(u.id));
  const rest = shown.filter((u) => !lead.includes(u) && !frequent.includes(u));

  const suggesting = lead.length > 0 || frequent.length > 0;
  const LABEL = { related: "people.related", frequent: "people.frequent", all: "people.all" } as const;

  const pick = (id: string) => {
    if (ready && rememberPicks && !sel.has(id)) recordPick(me, id);
    onToggle(id);
  };
  const forget = (id: string) => {
    removePick(me, id);
    setRemoved((prev) => new Set(prev).add(id));
    // The row moves out of the Frequent group and its × goes with it, so the
    // focus would fall to the page; the search box is where a keyboard user
    // carries on from (review round 1).
    searchRef.current?.focus();
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
          className="tag-remove"
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

  const section = (key: keyof typeof LABEL, people: User[], removable: boolean, first: boolean) =>
    people.length > 0 && (
      // A plain block, said inline: a host page may style every `ul > li` as a
      // bordered, padded row (My resources' `.page`), which on a section would
      // draw a second divider above 所有 and shrink the group to its content.
      <li key={key} style={{ display: "block", padding: 0, border: 0 }}>
        {/* The group role sits on a div: an <li> may not be one. */}
        <div role="group" aria-labelledby={labelIds[key]}>
          <div
            id={labelIds[key]}
            className="caps"
            style={{
              padding: "6px 8px 4px",
              ...(first ? {} : { marginTop: 4, borderTop: "1px solid var(--paper-3)" }),
            }}
          >
            {t(LABEL[key])}
          </div>
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>{people.map((u) => row(u, removable))}</ul>
        </div>
      </li>
    );

  return (
    <div style={{ minWidth: 240 }}>
      <input
        autoComplete="off"
        type="search"
        className="input"
        aria-labelledby={labelledBy}
        ref={searchRef}
        placeholder={placeholder ?? t("people.search")}
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
        {/* Before typing, a list that is neither alphabetical nor by id would
            read as a broken search, so each block says why it is there —
            相關 / 常用 / 所有, the way Teams heads its "Suggested" list. With
            nothing suggested there is nothing to tell apart, so no labels; once
            something is typed it is plain results. */}
        {suggesting ? (
          <>
            {section("related", lead, false, true)}
            {section("frequent", frequent, true, lead.length === 0)}
            {section("all", rest, false, false)}
          </>
        ) : (
          rest.map((u) => row(u, false))
        )}
        {shown.length === 0 && (
          <li style={{ padding: "6px 8px", color: "var(--text-paper-d)", fontSize: pxToRem(12) }}>
            {t("people.noMatches")}
          </li>
        )}
      </ul>
    </div>
  );
}
