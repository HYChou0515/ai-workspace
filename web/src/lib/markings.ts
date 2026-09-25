/**
 * Named markings (#847 Q5.1, Spotfire-style linked selection) — knowledge-free
 * (Q6): a marking is `column name → set of values`, both opaque strings. Which
 * columns link is each spec's `keys:`; views link on same-named columns.
 *
 * One store per item (`MarkingProvider`, inside `WorkspaceProviders`), kept in
 * step across the item's browser tabs (`markingsSync`), so the workspace and the
 * editor-area page see one set. Subscriptions are per NAME: a write to one
 * marking re-renders only the views on it.
 */

/** `column → values`. Values compare as strings; nothing is parsed. */
export type Marking = { readonly [column: string]: ReadonlySet<string> };

/** A marking as stored: what is marked, and the view file that last wrote it
 * (the `source` a `.markings/<name>.json` names for the AI, P7). */
export type MarkingEntry = { readonly marking: Marking; readonly source: string | null };

/** Whether `row` is lit by `marking`: it shares at least one column with the
 * marking, and on every shared column its value is in the set.
 *
 * "At least one": with none shared, "every shared column matches" is vacuously
 * true, which would light every row of a view keyed on something else. */
export function isLit(row: Readonly<Record<string, string>>, marking: Marking): boolean {
  let shared = false;
  for (const [column, values] of Object.entries(marking)) {
    if (!(column in row)) continue;
    shared = true;
    if (!values.has(row[column]!)) return false;
  }
  return shared;
}

/** What a selection writes: each key's distinct values over the selected rows.
 * `null` for a view without `keys:` — it can be lit, but cannot be a source. An
 * empty selection projects to `{}`, the write that clears the marking. */
export function projectOntoKeys(
  rows: readonly Readonly<Record<string, string>>[],
  keys: readonly string[],
): Marking | null {
  if (keys.length === 0) return null;
  const out: Record<string, Set<string>> = {};
  for (const row of rows) {
    for (const key of keys) {
      const v = row[key];
      if (v === undefined) continue;
      (out[key] ??= new Set()).add(v);
    }
  }
  return out;
}

function sameMarking(a: Marking, b: Marking): boolean {
  const cols = Object.keys(b).filter((c) => b[c]!.size > 0);
  if (cols.length !== Object.keys(a).length) return false;
  return cols.every((c) => {
    const x = a[c];
    const y = b[c]!;
    return x !== undefined && x.size === y.size && [...y].every((v) => x.has(v));
  });
}

function isEmpty(marking: Marking | null): boolean {
  return !marking || Object.values(marking).every((v) => v.size === 0);
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

  /** Write `name`. An empty marking (or `null`) clears it. The sets are copied:
   * a caller mutating its own set afterwards does not change the marking. */
  set(
    name: string,
    marking: Marking | null,
    source: string | null,
    opts: { fromPeer?: boolean; ifEmpty?: boolean } = {},
  ): void {
    const had = this.entries.has(name);
    // A seed (a view's `highlight:` on open) never overwrites a selection: the
    // check is HERE, at write time, because two views opened in one commit both
    // saw "empty" when they rendered.
    if (opts.ifEmpty && had) return;
    const held = this.entries.get(name);
    // The same write again changes nothing, so it tells no one: a view redrawn
    // with its new lit rows may report the same selection, and re-notifying
    // would redraw it, and so on without end.
    if (held && !isEmpty(marking) && held.source === source && sameMarking(held.marking, marking!)) {
      return;
    }
    if (isEmpty(marking)) {
      if (!had) return;
      this.entries.delete(name);
    } else {
      const copy: Record<string, ReadonlySet<string>> = {};
      for (const [column, values] of Object.entries(marking!)) {
        if (values.size > 0) copy[column] = new Set(values);
      }
      this.entries.set(name, { marking: copy, source });
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
