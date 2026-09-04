/**
 * ref-path traversal — a renderer concern, not the backend's (#419 §A4). The
 * projection emits a `ref` field as the raw target *number*; turning a
 * `milestone.title` column into the milestone's title (follow the ref, read a
 * field) happens here, at render time, from the loaded corpus. A ref whose
 * target is missing degrades to a marker rather than crashing the row (§D).
 */

import type { EntityFieldSpec, EntityInstance, EntityType } from "../../api/entities";
import { fieldText, roleOf } from "./shared";

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
    // And the types pointing BACK at this one (#785). A milestone's bar reaches
    // over its issues, which the view cannot know without their records — and
    // an unfetched corpus makes that feature a silent no-op rather than a
    // visible failure, because a union over nothing is just the original span.
    if (f.role === "backref" && f.from) seen.add(sourceOf(f.from)[0]);
  }
  return [...seen];
}

/** `"issue.milestone"` → `["issue", "milestone"]`: the type that points here and
 * the `ref` field on it that does the pointing. */
function sourceOf(from: string): [string, string] {
  const dot = from.indexOf(".");
  return dot < 0 ? [from, ""] : [from.slice(0, dot), from.slice(dot + 1)];
}

/** The records that point BACK at `record` through its `backref` fields — a
 * milestone's issues, say.
 *
 * Resolved from the loaded corpus rather than read off the record's projected
 * list, for the same reason ref traversal is done here at all: this file
 * already owns the relation at render time, and filtering works whether or not
 * the backend's projection ran. Compared numerically because the same
 * milestone is a string when a form wrote it and a number when the projection
 * did. */
export function backrefRecords(
  record: EntityInstance,
  type: EntityType | null,
  index: RefIndex,
): EntityInstance[] {
  const out: EntityInstance[] = [];
  for (const f of type?.fields ?? []) {
    if (f.role !== "backref" || !f.from) continue;
    const [srcType, srcField] = sourceOf(f.from);
    if (!srcField) continue;
    for (const r of index.get(srcType)?.values() ?? []) {
      if (Number(r.fields[srcField]) === record.number) out.push(r);
    }
  }
  return out;
}

/** The same relation as {@link backrefRecords}, for EVERY record at once:
 * target number → the records pointing at it.
 *
 * One pass over the corpus rather than one per row. A roadmap is milestones ×
 * issues, so asking each milestone to filter every issue is quadratic in two
 * numbers that both grow with the project — and it runs inside a render, on
 * every pointer move of a drag. Records pointing at nothing, or at a number
 * nothing has, simply land in no bucket. */
export function backrefBuckets(
  type: EntityType | null,
  index: RefIndex,
): Map<number, EntityInstance[]> {
  const buckets = new Map<number, EntityInstance[]>();
  for (const f of type?.fields ?? []) {
    if (f.role !== "backref" || !f.from) continue;
    const [srcType, srcField] = sourceOf(f.from);
    if (!srcField) continue;
    for (const r of index.get(srcType)?.values() ?? []) {
      const target = Number(r.fields[srcField]);
      if (!Number.isFinite(target)) continue;
      const bucket = buckets.get(target);
      if (bucket) bucket.push(r);
      else buckets.set(target, [r]);
    }
  }
  return buckets;
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

/** Picker options for a named field IF it's a `ref` (resolved from the type's
 * schema) — else `undefined`. Lets create + edit forms render a `#N <title>`
 * dropdown for a ref instead of a raw number box (the target lives in another
 * collection, so a bare number is meaningless to a human). `undefined` index ⇒
 * `undefined` (caller hasn't loaded referenced records → keep the number box). */
export function refOptionsForField(
  type: EntityType | null,
  index: RefIndex | undefined,
  name: string,
): RefOption[] | undefined {
  if (!index) return undefined;
  const spec = roleOf(type, name);
  return spec?.role === "ref" ? refOptions(spec, index) : undefined;
}
