/**
 * #455 P3 — pure helpers for the per-item activity feed. The global activity log
 * (`GET /activity`) records coarse events across all items; the feed scopes them
 * to one work item and resolves the workspace file a row opens when clicked.
 */

import type { EntityCatalog } from "../api/entities";
import type { ActivityEntry } from "../api/types";

/** Activity entries that belong to one item, in the backend's newest-first order. */
export function filterItemActivity(entries: ActivityEntry[], itemId: string): ActivityEntry[] {
  return entries.filter((e) => e.ref.investigation_id === itemId);
}

/** The workspace file an activity row opens when clicked, or null when it points
 * at nothing openable (e.g. an item-created / agent-turn event). A file event
 * carries its `path`; an entity write carries `{type, number}`, which resolves to
 * the record file `/{records_path}/{number}.md` via the catalog. */
export function activityOpenTarget(entry: ActivityEntry, catalog: EntityCatalog | undefined): string | null {
  if (entry.ref.path) return entry.ref.path;
  const { type, number } = entry.ref;
  if (type && number != null) {
    const recordsPath = catalog?.types.find((t) => t.name === type)?.records_path;
    if (recordsPath) return `/${recordsPath}/${number}.md`;
  }
  return null;
}
