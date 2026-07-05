/**
 * Local (client-side) table operations for the table view (#448 P5, §A1):
 * sorting + value filtering. "本地即可" — these run over the already-projected
 * records in memory, ephemeral to the open panel, so no server round-trip. Both
 * honour ref-traversal columns (`milestone.title`) via the same helper the
 * renderer uses.
 */

import type { EntityInstance, EntityType } from "../../api/entities";
import { traverseColumn, type RefIndex } from "./refTraversal";
import { fieldText, roleOf } from "./shared";

export type SortDir = "asc" | "desc";

const NUMERIC_ROLES = new Set(["progress", "rank", "ref"]);

/** The comparable key for a column: a ref-traversal column resolves to the
 * target's text; a numeric role compares numerically; everything else compares
 * as its display text. */
function sortKey(
  column: string,
  row: EntityInstance,
  type: EntityType | null,
  refIndex: RefIndex | undefined,
): string | number {
  const traversal = refIndex ? traverseColumn(column, row, type, refIndex) : null;
  // Case-insensitive so a human-facing sort reads alphabetically (a next to A).
  if (traversal) return traversal.text.toLowerCase();
  const spec = roleOf(type, column);
  const raw = row.fields[column];
  if (spec && NUMERIC_ROLES.has(spec.role)) {
    const n = Number(raw);
    return Number.isFinite(n) ? n : Number.NEGATIVE_INFINITY;
  }
  return fieldText(raw).toLowerCase();
}

export function sortEntities(
  entities: EntityInstance[],
  column: string,
  dir: SortDir,
  type: EntityType | null,
  refIndex: RefIndex | undefined,
): EntityInstance[] {
  return [...entities].sort((a, b) => {
    const ka = sortKey(column, a, type, refIndex);
    const kb = sortKey(column, b, type, refIndex);
    if (ka < kb) return dir === "asc" ? -1 : 1;
    if (ka > kb) return dir === "asc" ? 1 : -1;
    return a.number - b.number; // stable tiebreak by permanent id
  });
}

/** Keep rows whose column value equals each active filter (empty value = all).
 * Multiple active filters AND together. Ref-traversal columns match on the
 * resolved target text. */
export function filterEntities(
  entities: EntityInstance[],
  filters: Record<string, string>,
  type: EntityType | null,
  refIndex: RefIndex | undefined,
): EntityInstance[] {
  const active = Object.entries(filters).filter(([, v]) => v !== "");
  if (active.length === 0) return entities;
  return entities.filter((row) =>
    active.every(([column, value]) => {
      const traversal = refIndex ? traverseColumn(column, row, type, refIndex) : null;
      const text = traversal ? traversal.text : fieldText(row.fields[column]);
      return text === value;
    }),
  );
}
