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

export type CollectionEntry = { id: string; name: string };

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
          entries.push({ id, name: typeof rec.name === "string" ? rec.name : "" });
        }
        continue; // a duplicate id is redundant, not junk
      }
    }
    ignored += 1;
  }
  return { status: "ok", entries, selectedIds: entries.map((e) => e.id), ignored };
}

/** Serialize the chosen collections to the on-disk JSON — 2-space pretty-print,
 * only `id` + `name`, in the given order (git-friendly + matches hand edits). */
export function serializeCollectionsFile(selected: CollectionEntry[]): string {
  return JSON.stringify(
    selected.map((c) => ({ id: c.id, name: c.name })),
    null,
    2,
  );
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
