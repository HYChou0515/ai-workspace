/**
 * A table on a named marking (#847/#848 PR 5): what it shows, the bar above
 * its header that says why, and what selecting its rows writes.
 *
 * Reading (P1):
 * - The marking holds a set and shares a column with the table: only the rows
 *   it lights are shown, under "filtered by <name> · 3 of 25 rows · by lot ·
 *   show all" (P27: the columns it marks by — over two columns a marking
 *   lights every combination of their values).
 *   "show all" keeps every row and highlights the lit ones. The toggle is this
 *   person's, per view, kept in this browser like the header's marking choice
 *   (`useViewMarking`) — never written to the file.
 * - It shares no column: every row, and "no column in common with <name>".
 * - Empty, cleared, or no marking: every row, no bar.
 *
 * Writing (P2): on a marking, a row's checkbox is "this row is marked".
 * Checking or unchecking one writes the rows then checked, projected onto the
 * key columns — the spec's `keys:`, else the columns the marking already
 * holds — that this table has. A key value no row of this table carries keeps
 * the mark it had: the table decides only about the values it holds. With no
 * key column, nothing is written and `note` says
 * why (the header's marking control shows it). The table the selection was
 * made in is not filtered by it: it keeps every row with the marked ones
 * highlighted, so a click does not make the rest of the table vanish. Every
 * other view on the marking — a chart, another table — answers it.
 *
 * Rows are compared as marking text (`markingRows.ts`) by `isLit`, the
 * platform's one rule, so a table and a chart agree on which rows are marked.
 * Both the entity `table` and a plugin's table (`csv-table`, through the SDK)
 * use this one hook.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { useMarking } from "../../hooks/useMarking";
import { litRows, type MarkingRow } from "../../lib/markingRows";
import { markedBy, projectOntoKeys } from "../../lib/markings";

export type TableMarking = {
  /** Indices (into the rows given) to show, in order. */
  shown: number[];
  /** Rows to draw as marked: only while every row is shown ("show all", or
   * the selection was made here) — a filtered table shows nothing but lit rows. */
  highlighted: ReadonlySet<number>;
  /** The bar above the header, or null when there is nothing to say. */
  bar: React.ReactNode;
  /** Row selection as marking, or null on no marking (the table keeps its own
   * selection then). `checked` is the rows the marking lights; `set` writes the
   * rows to mark. `enabled` is false when the table has no key column to
   * write — `note` says why. */
  select: { checked: ReadonlySet<number>; enabled: boolean; set: (rows: ReadonlySet<number>) => void } | null;
  /** Why selecting rows here marks nothing, or null. */
  note: string | null;
};

function load(key: string | null): boolean {
  if (!key) return false;
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

/** "show all" for one view, kept in this browser. */
function useShowAll(viewKey: string | undefined): [boolean, (next: boolean) => void] {
  const key = viewKey ? `view-marking-all:${viewKey}` : null;
  // Remembers which view it was read for: a panel kept mounted while its file
  // changes must not carry one view's toggle to the next (as `useViewMarking`).
  const [state, setState] = useState(() => ({ key, on: load(key) }));
  if (state.key !== key) setState({ key, on: load(key) });
  const on = state.key === key ? state.on : load(key);
  const set = useCallback(
    (next: boolean) => {
      setState({ key, on: next });
      if (!key) return;
      try {
        if (next) localStorage.setItem(key, "1");
        else localStorage.removeItem(key);
      } catch {
        /* private mode / quota: the choice lasts this mount only */
      }
    },
    [key],
  );
  return [on, set];
}

const NONE: ReadonlySet<number> = new Set();

export function useTableMarking({
  marking,
  viewKey,
  rows,
  columns,
  keys = [],
  source = null,
  onNote,
}: {
  /** The marking the view is on (null / undefined: none). */
  marking: string | null | undefined;
  viewKey?: string;
  /** Every row the table would show, as marking text. */
  rows: readonly MarkingRow[];
  /** The table's columns — every one a row may carry, shown or not. */
  columns: readonly string[];
  /** The spec's `keys:` — the columns a selection here writes. */
  keys?: readonly string[];
  /** This view's file: the `source` of what it writes. */
  source?: string | null;
  /** Told `note` whenever it changes — the header's marking control shows it. */
  onNote?: (note: string | null) => void;
}): TableMarking {
  const name = marking ?? null;
  const [entry, write] = useMarking(name);
  const [showAll, setShowAll] = useShowAll(viewKey);
  const held = entry?.marking;
  const shared = useMemo(
    () => (held ? Object.keys(held).filter((c) => columns.includes(c)) : []),
    [held, columns],
  );
  const lit = useMemo(() => (held && shared.length > 0 ? litRows(rows, held) : null), [held, shared, rows]);
  const all = useMemo(() => rows.map((_, i) => i), [rows]);
  const litIdx = useMemo(() => (lit ? all.filter((i) => lit[i]) : []), [all, lit]);

  // What a selection writes: `keys:`, else the marking's own columns — only
  // those this table has (a projection onto none is `{}`, the write that
  // CLEARS the marking, which a table that cannot name a row must never send).
  const writeKeys = (keys.length > 0 ? keys : held ? Object.keys(held) : []).filter((c) => columns.includes(c));
  const note = !name
    ? null
    : writeKeys.length > 0
      ? null
      : keys.length > 0 || held
        ? `Selecting rows marks nothing: this table has none of ${name}'s columns.`
        : `Selecting rows marks nothing: add keys: to this view, or mark ${name} elsewhere first.`;
  useEffect(() => {
    onNote?.(note);
  }, [onNote, note]);
  useEffect(() => () => onNote?.(null), [onNote]);

  const set = (chosen: ReadonlySet<number>) => {
    if (writeKeys.length === 0) return;
    const next = projectOntoKeys([...chosen].map((i) => rows[i]!), writeKeys)! as Record<string, Set<string>>;
    // A table decides only about the values it holds: a key value no row of
    // it carries (a lot only a wider chart has, or a row its own value filter
    // hides) keeps the mark it had, or ticking one box would silently unmark
    // points elsewhere.
    for (const c of writeKeys) {
      const carried = new Set(rows.map((r) => r[c]));
      for (const v of held?.[c] ?? []) if (!carried.has(v)) (next[c] ??= new Set()).add(v);
    }
    write(next, source);
  };
  const select = name ? { checked: lit ? new Set(litIdx) : NONE, enabled: writeKeys.length > 0, set } : null;

  if (!name || !held) return { shown: all, highlighted: NONE, bar: null, select, note };
  if (!lit) {
    return {
      shown: all,
      highlighted: NONE,
      bar: <MarkingBar>no column in common with {name}</MarkingBar>,
      select,
      note,
    };
  }
  const count = `${litIdx.length} of ${rows.length} rows · ${markedBy(held)}`;
  // The selection was made here: every row stays, the marked ones highlighted.
  const madeHere = source !== null && entry.source === source;
  if (madeHere || showAll) {
    return {
      shown: all,
      highlighted: new Set(litIdx),
      bar: (
        <MarkingBar>
          {name} marks {count}
          {!madeHere && (
            <>
              {" · "}
              <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={() => setShowAll(false)}>
                show marked only
              </button>
            </>
          )}
        </MarkingBar>
      ),
      select,
      note,
    };
  }
  return {
    shown: litIdx,
    highlighted: NONE,
    bar: (
      <MarkingBar>
        filtered by {name} · {count} ·{" "}
        <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={() => setShowAll(true)}>
          show all
        </button>
      </MarkingBar>
    ),
    select,
    note,
  };
}

function MarkingBar({ children }: { children: React.ReactNode }) {
  return (
    <div role="status" aria-label="marking filter" className="ev-marking-bar">
      {children}
    </div>
  );
}
