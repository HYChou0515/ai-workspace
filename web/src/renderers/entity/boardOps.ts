/**
 * Board column + drag logic (#451, §A3) — pure so it's unit-testable without
 * simulating a real @dnd-kit drag gesture (the gesture is the library's job; the
 * *outcome* — which status a drop writes — is ours).
 */

import type { EntityFieldSpec, EntityInstance } from "../../api/entities";
import { fieldText } from "./shared";

/** The synthetic "no status" column id (droppable → clears the field). */
export const UNSET_COL = "__unset__";

/** Distinct non-empty display values of a field across the records. */
function distinct(entities: EntityInstance[], field: string): string[] {
  const seen = new Set<string>();
  for (const e of entities) {
    const v = fieldText(e.fields[field]);
    if (v) seen.add(v);
  }
  return [...seen];
}

/** Partition the board's columns: `known` = the status field's closed vocabulary
 * (drop targets); `extra` = values present in the data but outside the vocab —
 * a lint warning (§D) shown in their own degraded, non-droppable columns so the
 * cards stay visible instead of vanishing. With no closed vocab, every present
 * value is a normal column. */
export function partitionColumns(
  statusSpec: EntityFieldSpec | undefined,
  entities: EntityInstance[],
  groupField: string,
): { known: string[]; extra: string[] } {
  const known = statusSpec?.values ?? [];
  const present = distinct(entities, groupField);
  if (known.length === 0) return { known: present, extra: [] };
  return { known, extra: present.filter((v) => !known.includes(v)) };
}

/** The patch a drop produces: onto the unset column → clear the field; onto a
 * value column → set it. `null` = no-op (dropped outside any column). */
export function dropPatch(
  activeId: string,
  overId: string | null,
  groupField: string,
): { number: number; patch: Record<string, unknown> } | null {
  if (!overId) return null;
  const card = /^card-(\d+)$/.exec(activeId);
  if (!card) return null;
  const number = Number(card[1]);
  if (overId === `col-${UNSET_COL}`) return { number, patch: { [groupField]: null } };
  const col = /^col-(.+)$/.exec(overId);
  if (!col) return null;
  return { number, patch: { [groupField]: col[1] } };
}

/** @dnd-kit `onDragEnd` → the single write path. Kept tiny + pure so the
 * drag *outcome* is fully tested; only the gesture is delegated to the library. */
export function handleDragEnd(
  event: { active: { id: string | number }; over: { id: string | number } | null },
  groupField: string,
  onPatch: (number: number, patch: Record<string, unknown>) => void,
): void {
  const patch = dropPatch(String(event.active.id), event.over ? String(event.over.id) : null, groupField);
  if (patch) onPatch(patch.number, patch.patch);
}
