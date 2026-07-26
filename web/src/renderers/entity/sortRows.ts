/**
 * Multi-level, role-aware ordering for entity rows (#GH-projects, plan P2).
 *
 * A view carries an optional list of sort tiers (`spec.sort`) and, when there is
 * none, falls back to the manual `rank` order (the drag-to-reorder position). The
 * comparator sorts by MEANING, not raw storage: a `status` orders by its declared
 * vocabulary (open before done, not alphabetically), a `ref` by the referenced
 * record's title, an `actor` by the person's name, dates/numbers naturally. Each
 * tier breaks ties for the previous one; the final tie-break is always the manual
 * `rank` (then the record number) so the order is stable and deterministic.
 *
 * Pure — the view components just feed it their rows. Missing values sort LAST in
 * either direction (empty cells collect at the bottom, GitHub-style).
 */

import type { EntityInstance, EntityType } from "../../api/entities";
import type { User } from "../../api/types";
import type { RefIndex } from "./refTraversal";
import { fieldText, roleOf } from "./shared";
import type { SortRule } from "./types";

function isMissing(e: EntityInstance, field: string): boolean {
  const v = e.fields[field];
  return v == null || v === "";
}

/** A comparable key for one field's value, resolved through its role. */
function sortKey(
  e: EntityInstance,
  field: string,
  type: EntityType | null,
  refIndex: RefIndex | undefined,
  users: User[] | undefined,
): string | number {
  const raw = e.fields[field];
  const spec = roleOf(type, field);
  switch (spec?.role) {
    case "status": {
      // Order by the declared vocabulary, not alphabetically; unknown values last.
      const i = spec.values?.indexOf(String(raw)) ?? -1;
      return i >= 0 ? i : Number.MAX_SAFE_INTEGER;
    }
    case "ref": {
      const num = Number(raw);
      const target = spec.to ? refIndex?.get(spec.to)?.get(num) : undefined;
      return (target ? fieldText(target.fields.title) : "").toLowerCase() || `￿${num}`;
    }
    case "actor": {
      const id = fieldText(raw);
      return (users?.find((u) => u.id === id)?.name ?? id).toLowerCase();
    }
    case "daterange":
      return String(raw).slice(0, 10); // start date; ISO strings sort lexically
    case "date":
      return String(raw);
    case "progress":
    case "rank":
      return Number(raw);
    default:
      return fieldText(raw).toLowerCase();
  }
}

function compareField(
  a: EntityInstance,
  b: EntityInstance,
  rule: SortRule,
  type: EntityType | null,
  refIndex: RefIndex | undefined,
  users: User[] | undefined,
): number {
  const ma = isMissing(a, rule.field);
  const mb = isMissing(b, rule.field);
  if (ma && mb) return 0;
  if (ma) return 1; // missing sorts last, regardless of direction
  if (mb) return -1;
  const ka = sortKey(a, rule.field, type, refIndex, users);
  const kb = sortKey(b, rule.field, type, refIndex, users);
  const cmp = ka < kb ? -1 : ka > kb ? 1 : 0;
  return rule.dir === "desc" ? -cmp : cmp;
}

/** The manual position: the `rank` field, falling back to the record number for a
 * record that has none yet (so un-ranked records still order deterministically). */
function rankOf(e: EntityInstance): number {
  const r = Number(e.fields.rank);
  return Number.isFinite(r) ? r : e.number;
}

/** Order rows by the view's sort tiers, or — when there are none — by the manual
 * `rank`. Returns a new array; never mutates the input. */
export function sortRows(
  rows: EntityInstance[],
  sort: SortRule[] | undefined,
  type: EntityType | null,
  refIndex: RefIndex | undefined,
  users: User[] | undefined,
): EntityInstance[] {
  const copy = [...rows];
  if (!sort || sort.length === 0) {
    copy.sort((a, b) => rankOf(a) - rankOf(b) || a.number - b.number);
    return copy;
  }
  copy.sort((a, b) => {
    for (const rule of sort) {
      const c = compareField(a, b, rule, type, refIndex, users);
      if (c !== 0) return c;
    }
    return rankOf(a) - rankOf(b) || a.number - b.number; // stable final tie-break
  });
  return copy;
}
