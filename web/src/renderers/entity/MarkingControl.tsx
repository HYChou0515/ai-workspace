/**
 * #847 PR 3 P3: the marking control (a tag icon + `<name> ▾`) in a view's header — attach the view to an
 * existing marking, to a new one, or detach it.
 *
 * The choice is VIEW STATE, kept per view in this browser (like a gantt's
 * collapsed groups): the file is never rewritten, because where someone points
 * a view while exploring is not a decision for everyone who opens the file.
 * They edit `marking:` in the YAML to make it permanent.
 */
import { useCallback, useState } from "react";

import { Icon } from "../../components/Icon";
import { useMarkingNames } from "../../hooks/useMarking";

/** The select's value that opens the new-name box — never a marking name
 * (names are file names, and cannot hold NUL). */
const NEW = "\u0000new";

/** The marking a view is on: the file's until this person chose otherwise. */
export function useViewMarking(
  viewKey: string | undefined,
  fromFile: string | null,
): [string | null, (next: string | null) => void] {
  const storageKey = viewKey ? `view-marking:${viewKey}` : null;
  // The state remembers WHICH view it was loaded for: the IDE keeps one panel
  // mounted and swaps the file under it, and a choice read once would follow
  // the panel to the next file (the class `usePersistentSet` fixed). A new key
  // re-reads during the render that sees it.
  const [state, setState] = useState(() => ({ key: storageKey, chosen: load(storageKey) }));
  if (state.key !== storageKey) setState({ key: storageKey, chosen: load(storageKey) });
  const chosen = state.key === storageKey ? state.chosen : load(storageKey);
  const choose = useCallback(
    (name: string | null) => {
      setState({ key: storageKey, chosen: { name } });
      if (!storageKey) return;
      try {
        localStorage.setItem(storageKey, JSON.stringify({ name }));
      } catch {
        /* private mode / quota: the choice lasts this mount only */
      }
    },
    [storageKey],
  );
  return [chosen ? chosen.name : fromFile, choose];
}

function load(storageKey: string | null): { name: string | null } | null {
  if (!storageKey) return null;
  try {
    const raw = localStorage.getItem(storageKey);
    return raw ? (JSON.parse(raw) as { name: string | null }) : null;
  } catch {
    return null;
  }
}

export function MarkingControl({
  value,
  onChange,
  note = null,
}: {
  value: string | null;
  onChange: (next: string | null) => void;
  /** Why selecting in this view marks nothing (#847/#848 PR 5), or null. */
  note?: string | null;
}) {
  const names = useMarkingNames();
  const [naming, setNaming] = useState(false);
  const [draft, setDraft] = useState("");
  const options = [...new Set([...(value ? [value] : []), ...names])].sort();
  return (
    <span className="ev-marking" style={{ display: "inline-flex", flexWrap: "wrap", alignItems: "center", gap: 4 }}>
      <Icon name="tag" size={12} color="var(--text-paper-d)" />
      <select
        aria-label="Marking"
        className="input"
        value={naming ? NEW : (value ?? "")}
        onChange={(e) => {
          const v = e.target.value;
          if (v === NEW) {
            setNaming(true);
            return;
          }
          setNaming(false);
          onChange(v || null);
        }}
      >
        <option value="">not linked</option>
        {options.map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
        <option value={NEW}>New marking…</option>
      </select>
      {naming && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const name = draft.trim();
            if (!name) return;
            setNaming(false);
            setDraft("");
            onChange(name);
          }}
          style={{ display: "inline-flex", gap: 4 }}
        >
          <input
            aria-label="New marking name"
            className="input"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit">Link</button>
        </form>
      )}
      {note && (
        <span role="note" className="ev-marking__note">
          {note}
        </span>
      )}
    </span>
  );
}
