/**
 * Named markings (#847 Q5.1, Spotfire-style linked selection) — knowledge-free
 * (Q6): a marking is the picked rows' values on its key columns, all opaque
 * strings (#861). Which columns link is each spec's `keys:`; views link on
 * same-named columns.
 *
 * One store per item (`MarkingProvider`, inside `WorkspaceProviders`), kept in
 * step across the item's browser tabs (`markingsSync`), so the workspace and the
 * editor-area page see one set. Subscriptions are per NAME: a write to one
 * marking re-renders only the views on it.
 */

/** The picked rows (#861 D1): their values on `keys`, one tuple per pick.
 * `keys` are sorted — two views writing the same columns in a different
 * `keys:` order hold one marking — and a tuple is its values in that order
 * joined by `SEP`. Build one with `markingFrom`; never assemble the text. */
export type Marking = { readonly keys: readonly string[]; readonly tuples: ReadonlySet<string> };

/** A marking as stored: what is marked, and the view file that last wrote it
 * (the `source` a `.markings/<name>.json` names for the AI, P7). */
export type MarkingEntry = { readonly marking: Marking; readonly source: string | null };

/** U+001F (unit separator): no cell text a person or a CSV writes holds it, so
 * `["a,b", "c"]` and `["a", "b,c"]` stay two tuples. */
const SEP = "\u001f";

/** The one constructor: keys sorted with each row's values moved alongside,
 * a row of the wrong length dropped, duplicates dropped. */
export function markingFrom(keys: readonly string[], rows: readonly (readonly string[])[]): Marking {
  const order = keys.map((k, i) => [k, i] as const).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const tuples = new Set<string>();
  for (const row of rows) {
    if (row.length !== keys.length) continue;
    tuples.add(order.map(([, i]) => row[i]!).join(SEP));
  }
  return { keys: order.map(([k]) => k), tuples };
}

/** The picked rows as `string[][]` (the wire and file shape), sorted. */
export function markingRows(marking: Marking): string[][] {
  return [...marking.tuples].sort().map((t) => t.split(SEP));
}

/** How many rows were picked — what every count says (#861 D5). */
export function markingSize(marking: Marking): number {
  return marking.tuples.size;
}

// A coarser row's lookup (D2): the picks projected onto the keys it has, built
// once per marking and key subset — `isLit` runs once per drawn row.
const projections = new WeakMap<Marking, Map<string, ReadonlySet<string>>>();

function projected(marking: Marking, positions: readonly number[]): ReadonlySet<string> {
  let byShape = projections.get(marking);
  if (!byShape) projections.set(marking, (byShape = new Map()));
  const shape = positions.join(",");
  let set = byShape.get(shape);
  if (!set) {
    const out = new Set<string>();
    for (const t of marking.tuples) {
      const values = t.split(SEP);
      out.add(positions.map((i) => values[i]!).join(SEP));
    }
    byShape.set(shape, (set = out));
  }
  return set;
}

/** Whether `row` is lit by `marking`.
 *
 * - It has every key: lit iff it IS one of the picks (#861 D1).
 * - It has some keys (a view coarser than the marking, e.g. a per-group
 *   summary beside a per-item gallery): lit iff it contains a pick — its values
 *   match a pick projected onto the keys it has (D2, Spotfire's relation).
 * - It has none: not lit. "Matches on every shared key" is vacuously true with
 *   none shared, which would light every row of a view keyed on something else;
 *   a caller draws such a view undimmed instead. */
export function isLit(row: Readonly<Record<string, string>>, marking: Marking): boolean {
  const positions: number[] = [];
  const values: string[] = [];
  marking.keys.forEach((k, i) => {
    const v = row[k];
    if (v === undefined) return;
    positions.push(i);
    values.push(v);
  });
  if (positions.length === 0) return false;
  const text = values.join(SEP);
  if (positions.length === marking.keys.length) return marking.tuples.has(text);
  return projected(marking, positions).has(text);
}

/** The columns a marking marks by, said next to a count: "by group, item"
 * (#847/#848 PR 5 P27). Since #861 the count is the picks, so this says which
 * columns link the views, not why a count ran high. */
export function markedBy(marking: Marking): string {
  return `by ${marking.keys.join(", ")}`;
}

/** What a selection writes: each selected row's `keys` values as one pick.
 * `null` for a view without `keys:` — it can be lit, but cannot be a source. A
 * row missing a key names no whole pick and is skipped. An empty selection is an
 * empty marking, the write that clears. */
export function projectOntoKeys(
  rows: readonly Readonly<Record<string, string>>[],
  keys: readonly string[],
): Marking | null {
  if (keys.length === 0) return null;
  const picks: string[][] = [];
  for (const row of rows) {
    const values = keys.map((k) => row[k]);
    if (values.every((v): v is string => v !== undefined)) picks.push(values);
  }
  return markingFrom(keys, picks);
}

function sameMarking(a: Marking, b: Marking): boolean {
  return (
    a.keys.length === b.keys.length &&
    a.keys.every((k, i) => k === b.keys[i]) &&
    a.tuples.size === b.tuples.size &&
    [...b.tuples].every((t) => a.tuples.has(t))
  );
}

function isEmpty(marking: Marking | null): boolean {
  return !marking || marking.tuples.size === 0;
}

export class MarkingStore {
  private entries = new Map<string, MarkingEntry>();
  private listeners = new Map<string, Set<() => void>>();
  private nameListeners = new Set<() => void>();
  private nameList: readonly string[] = [];
  private allListeners = new Set<() => void>();
  private all: ReadonlyMap<string, MarkingEntry> = new Map();
  private writeListeners = new Set<
    (name: string, entry: MarkingEntry | undefined, fromPeer: boolean) => void
  >();

  get(name: string): MarkingEntry | undefined {
    return this.entries.get(name);
  }

  /** Names holding a non-empty marking, sorted — stable between writes. */
  names(): readonly string[] {
    return this.nameList;
  }

  /** Write `name`. An empty marking (or `null`) clears it. The marking is
   * copied: a caller mutating its own afterwards does not change it.
   * Returns whether the marking holds the write (#847/#848 PR 5 P41 row 27):
   * false only when `ifEmpty` found it occupied and nothing was written. The
   * same write again is held already -- true, and silent: it tells no one. */
  set(
    name: string,
    marking: Marking | null,
    source: string | null,
    opts: { fromPeer?: boolean; ifEmpty?: boolean } = {},
  ): boolean {
    const had = this.entries.has(name);
    // A seed (a view's `highlight:` on open) never overwrites a selection: the
    // check is HERE, at write time, because two views opened in one commit both
    // saw "empty" when they rendered.
    if (opts.ifEmpty && had) return false;
    const held = this.entries.get(name);
    // The same write again changes nothing, so it tells no one: a view redrawn
    // with its new lit rows may report the same selection, and re-notifying
    // would redraw it, and so on without end.
    if (held && !isEmpty(marking) && held.source === source && sameMarking(held.marking, marking!)) {
      return true;
    }
    if (isEmpty(marking)) {
      if (!had) return true;
      this.entries.delete(name);
    } else {
      // Copied: a caller keeping its own set cannot change the marking later.
      this.entries.set(name, { marking: { keys: [...marking!.keys], tuples: new Set(marking!.tuples) }, source });
    }
    for (const cb of this.listeners.get(name) ?? []) cb();
    this.all = new Map([...this.entries].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
    for (const cb of this.allListeners) cb();
    if (had !== this.entries.has(name)) {
      this.nameList = [...this.entries.keys()].sort();
      for (const cb of this.nameListeners) cb();
    }
    const entry = this.entries.get(name);
    for (const cb of this.writeListeners) cb(name, entry, opts.fromPeer ?? false);
    return true;
  }

  /** Every write, with whether it came from another tab (`markingsSync`). */
  subscribeWrites(
    cb: (name: string, entry: MarkingEntry | undefined, fromPeer: boolean) => void,
  ): () => void {
    this.writeListeners.add(cb);
    return () => {
      this.writeListeners.delete(cb);
    };
  }

  subscribe(name: string, cb: () => void): () => void {
    let set = this.listeners.get(name);
    if (!set) this.listeners.set(name, (set = new Set()));
    set.add(cb);
    return () => {
      set.delete(cb);
    };
  }

  /** Every marking, sorted by name — a new object only after a write. For the
   * composer, which shows them all; a VIEW subscribes to its one name instead. */
  snapshot(): ReadonlyMap<string, MarkingEntry> {
    return this.all;
  }

  subscribeAll(cb: () => void): () => void {
    this.allListeners.add(cb);
    return () => {
      this.allListeners.delete(cb);
    };
  }

  subscribeNames(cb: () => void): () => void {
    this.nameListeners.add(cb);
    return () => {
      this.nameListeners.delete(cb);
    };
  }
}
