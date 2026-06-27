/**
 * Pure helpers for a Topic Hub item's `collections.json` (topic-hub §5, #142).
 *
 * The file is hand-editable JSON (`[{id,name}]`), so parsing mirrors the backend
 * `kb/collections.py::collection_ids_from_json` tolerance: a non-array is rejected,
 * a malformed entry is dropped rather than crashing. The picker layers a little
 * UX state on top (missing vs invalid vs ignored-count) so it can warn before it
 * overwrites a file someone may be hand-editing in Monaco.
 */

import type { KbCollection } from "../api/kb";

/** A selected collection. `tier` (#280) is an optional priority tier (sparse ints
 * — 0, 10, 20 — so groups can be inserted between later); absent ⇒ tier 0. The agent
 * walks tiers by rank (ascending), so the integer values only matter for ordering. */
export type CollectionEntry = { id: string; name: string; tier?: number };

export type CollectionsFileParse = {
  /** `missing` = no/blank file (empty selection, no warning); `invalid` = whole
   * file unparseable or not an array (warn before overwrite); `ok` = parsed. */
  status: "ok" | "missing" | "invalid";
  /** Kept `{id,name}` entries in file order, de-duped on id. `name` is "" when
   * the entry omitted it — display falls back to the live name by id. */
  entries: CollectionEntry[];
  /** Convenience: `entries.map(e => e.id)`. */
  selectedIds: string[];
  /** How many entries were dropped as malformed (only meaningful for `ok`). */
  ignored: number;
};

/** Parse raw `collections.json` content (or `null` for a missing file). */
export function parseCollectionsFile(content: string | null): CollectionsFileParse {
  if (content === null || content.trim() === "") {
    return { status: "missing", entries: [], selectedIds: [], ignored: 0 };
  }
  let data: unknown;
  try {
    data = JSON.parse(content);
  } catch {
    return { status: "invalid", entries: [], selectedIds: [], ignored: 0 };
  }
  if (!Array.isArray(data)) {
    return { status: "invalid", entries: [], selectedIds: [], ignored: 0 };
  }
  const seen = new Set<string>();
  const entries: CollectionEntry[] = [];
  let ignored = 0;
  for (const raw of data) {
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      const rec = raw as Record<string, unknown>;
      const id = rec.id;
      if (typeof id === "string" && id) {
        if (!seen.has(id)) {
          seen.add(id);
          const name = typeof rec.name === "string" ? rec.name : "";
          // Tolerant like the backend: only a real integer tier is kept (a
          // hand-typed "10" / junk is dropped, leaving tier 0). `Number.isInteger`
          // rejects booleans + floats; absent ⇒ no `tier` key ⇒ treated as 0.
          const entry: CollectionEntry = Number.isInteger(rec.tier)
            ? { id, name, tier: rec.tier as number }
            : { id, name };
          entries.push(entry);
        }
        continue; // a duplicate id is redundant, not junk
      }
    }
    ignored += 1;
  }
  return { status: "ok", entries, selectedIds: entries.map((e) => e.id), ignored };
}

/** Serialize the chosen collections to the on-disk JSON — 2-space pretty-print, in
 * the given order (git-friendly + matches hand edits). Emits `id` + `name`, plus
 * `tier` only when it is a non-zero integer — so a single-tier (flat) selection stays
 * the flat `[{id,name}]` file it always was, and only real tiering adds the key. */
export function serializeCollectionsFile(selected: CollectionEntry[]): string {
  return JSON.stringify(
    selected.map((c) =>
      Number.isInteger(c.tier) && c.tier !== 0
        ? { id: c.id, name: c.name, tier: c.tier }
        : { id: c.id, name: c.name },
    ),
    null,
    2,
  );
}

/** Group entries into ordered priority tiers for the picker UI (#280): distinct tier
 * values (absent ⇒ 0) sorted ascending, each group's entries kept in file order. The
 * group's index is its **rank** (rank 0 = highest priority). */
export function groupEntriesByTier(entries: CollectionEntry[]): CollectionEntry[][] {
  const byTier = new Map<number, CollectionEntry[]>();
  for (const e of entries) {
    const tier = Number.isInteger(e.tier) ? (e.tier as number) : 0;
    const group = byTier.get(tier);
    if (group) group.push(e);
    else byTier.set(tier, [e]);
  }
  return [...byTier.keys()].sort((a, b) => a - b).map((t) => byTier.get(t)!);
}

/** Flatten the picker's ordered groups back into entries, stamping each with a sparse
 * tier int (group 0 → 0, group 1 → 10, …) so an operator can later hand-insert a tier
 * between them. Empty groups are dropped so emptying a tier doesn't leave a rank gap. */
export function entriesFromGroups(groups: CollectionEntry[][]): CollectionEntry[] {
  const out: CollectionEntry[] = [];
  let rank = 0;
  for (const group of groups) {
    if (group.length === 0) continue; // a dropped group must not shift ranks
    const tier = rank * 10;
    for (const e of group) out.push({ id: e.id, name: e.name, tier });
    rank += 1;
  }
  return out;
}

/** Split selected ids into ones present in the live collection list vs orphans
 * (ids whose collection was deleted — shown for explicit removal, never auto-dropped). */
export function splitSelection(
  selectedIds: string[],
  available: KbCollection[],
): { known: string[]; orphans: string[] } {
  const live = new Set(available.map((c) => c.resource_id));
  const known: string[] = [];
  const orphans: string[] = [];
  for (const id of selectedIds) (live.has(id) ? known : orphans).push(id);
  return { known, orphans };
}
