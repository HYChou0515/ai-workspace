/**
 * A table on a named marking (#847/#848 PR 5): what it shows, and the bar
 * above its header that says why.
 *
 * - The marking holds a set and shares a column with the table: only the rows
 *   it lights are shown, under "filtered by <name> · 3 of 25 rows · show all".
 *   "show all" keeps every row and highlights the lit ones. The toggle is this
 *   person's, per view, kept in this browser like the header's marking choice
 *   (`useViewMarking`) — never written to the file.
 * - It shares no column: every row, and "no column in common with <name>".
 * - Empty, cleared, or no marking: every row, no bar.
 *
 * Rows are compared as marking text (`markingRows.ts`) by `isLit`, the
 * platform's one rule, so a table and a chart agree on which rows are marked.
 * Both the entity `table` and a plugin's table (`csv-table`, through the SDK)
 * use this one hook.
 */
import { useCallback, useMemo, useState } from "react";

import { useMarking } from "../../hooks/useMarking";
import { litRows, type MarkingRow } from "../../lib/markingRows";

export type TableMarking = {
  /** Indices (into the rows given) to show, in order. */
  shown: number[];
  /** Per row, whether to draw it as marked: only while every row is shown
   * ("show all") — a filtered table shows nothing but lit rows. */
  highlighted: ReadonlySet<number>;
  /** The bar above the header, or null when there is nothing to say. */
  bar: React.ReactNode;
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

export function useTableMarking({
  marking,
  viewKey,
  rows,
  columns,
}: {
  /** The marking the view is on (null / undefined: none). */
  marking: string | null | undefined;
  viewKey?: string;
  /** Every row the table would show, as marking text. */
  rows: readonly MarkingRow[];
  /** The table's columns — every one a row may carry, shown or not. */
  columns: readonly string[];
}): TableMarking {
  const name = marking ?? null;
  const [entry] = useMarking(name);
  const [showAll, setShowAll] = useShowAll(viewKey);
  const held = entry?.marking;
  const shared = useMemo(
    () => (held ? Object.keys(held).filter((c) => columns.includes(c)) : []),
    [held, columns],
  );
  const lit = useMemo(() => (held && shared.length > 0 ? litRows(rows, held) : null), [held, shared, rows]);
  const all = useMemo(() => rows.map((_, i) => i), [rows]);

  if (!name || !held) return { shown: all, highlighted: new Set(), bar: null };
  if (!lit) {
    return {
      shown: all,
      highlighted: new Set(),
      bar: <MarkingBar>no column in common with {name}</MarkingBar>,
    };
  }
  const litIdx = all.filter((i) => lit[i]);
  const count = `${litIdx.length} of ${rows.length} rows`;
  if (showAll) {
    return {
      shown: all,
      highlighted: new Set(litIdx),
      bar: (
        <MarkingBar>
          {name} marks {count} ·{" "}
          <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={() => setShowAll(false)}>
            show marked only
          </button>
        </MarkingBar>
      ),
    };
  }
  return {
    shown: litIdx,
    highlighted: new Set(),
    bar: (
      <MarkingBar>
        filtered by {name} · {count} ·{" "}
        <button type="button" className="btn" data-variant="ghost" data-size="sm" onClick={() => setShowAll(true)}>
          show all
        </button>
      </MarkingBar>
    ),
  };
}

function MarkingBar({ children }: { children: React.ReactNode }) {
  return (
    <div role="status" aria-label="marking filter" className="ev-marking-bar">
      {children}
    </div>
  );
}
