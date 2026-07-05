/**
 * ref-path traversal — a renderer concern, not the backend's (#419 §A4). The
 * projection emits a `ref` field as the raw target *number*; turning a
 * `milestone.title` column into the milestone's title (follow the ref, read a
 * field) happens here, at render time, from the loaded corpus. A ref whose
 * target is missing degrades to a marker rather than crashing the row (§D).
 */

import type { EntityFieldSpec, EntityInstance, EntityType } from "../../api/entities";
import { fieldText } from "./shared";

/** type name → (record number → record), for O(1) ref resolution. */
export type RefIndex = Map<string, Map<number, EntityInstance>>;

export type RefOption = { number: number; label: string };

/** The target types the entity's `ref` fields point at — the record lists a view
 * must also load to resolve ref-traversal columns + populate ref pickers. */
export function referencedTypes(type: EntityType | null): string[] {
  if (!type) return [];
  const seen = new Set<string>();
  for (const f of type.fields) {
    if (f.role === "ref" && f.to) seen.add(f.to);
  }
  return [...seen];
}

export function buildRefIndex(recordsByType: Record<string, EntityInstance[]>): RefIndex {
  const index: RefIndex = new Map();
  for (const [t, records] of Object.entries(recordsByType)) {
    index.set(t, new Map(records.map((r) => [r.number, r])));
  }
  return index;
}

export type TraversalResult = { text: string; dangling: boolean };

/** Resolve a dotted `refField.subField` column for a record: follow the record's
 * ref (a target number) into the ref's `to` type and read `subField`. Returns
 * `null` when the column isn't a ref-traversal (not dotted, or the base field
 * isn't a ref) so the caller renders it as an ordinary field. */
export function traverseColumn(
  column: string,
  record: EntityInstance,
  type: EntityType | null,
  index: RefIndex,
): TraversalResult | null {
  const dot = column.indexOf(".");
  if (dot < 0) return null;
  const refName = column.slice(0, dot);
  const subField = column.slice(dot + 1);
  const refSpec = type?.fields.find((f) => f.name === refName);
  if (!refSpec || refSpec.role !== "ref" || !refSpec.to) return null;

  const raw = record.fields[refName];
  if (raw == null || raw === "") return { text: "", dangling: false };
  const num = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(num)) return { text: "", dangling: false };

  const target = index.get(refSpec.to)?.get(num);
  if (!target) return { text: `#${num}?`, dangling: true };
  return { text: fieldText(target.fields[subField]), dangling: false };
}

/** The picker options for a `ref` field — every record of the target type shown
 * as `#N <title>` (falling back to `#N` when the target has no title). */
export function refOptions(refSpec: EntityFieldSpec, index: RefIndex): RefOption[] {
  if (!refSpec.to) return [];
  const targets = index.get(refSpec.to);
  if (!targets) return [];
  return [...targets.values()].map((r) => ({
    number: r.number,
    label: fieldText(r.fields.title) || `#${r.number}`,
  }));
}
