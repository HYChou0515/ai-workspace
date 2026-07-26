/**
 * Board column + drag logic (#451, §A3) — pure so it's unit-testable without
 * simulating a real @dnd-kit drag gesture (the gesture is the library's job; the
 * *outcome* — which status a drop writes — is ours).
 */

import type { EntityFieldSpec, EntityInstance } from "../../api/entities";
import { fieldText } from "./shared";
import { rankForDrop, rankOf } from "./sortRows";

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

/** The full outcome of a board drop (#GH-projects P4): dropping onto a COLUMN
 * changes the status (`dropPatch`); dropping onto a CARD adopts that card's
 * status AND — unless a sort is active — reorders in front of it by writing a
 * fractional `rank` (manual order). `null` = no-op. Pure, so the drag outcome is
 * fully tested; the gesture stays the library's job. */
export function dropResult(
  activeId: string,
  overId: string | null,
  groupField: string,
  entities: EntityInstance[],
  sortActive: boolean,
): { number: number; patch: Record<string, unknown> } | null {
  const col = dropPatch(activeId, overId, groupField);
  if (col) return col; // dropped on a column / the unset column
  if (!overId) return null;
  const am = /^card-(\d+)$/.exec(activeId);
  const om = /^card-(\d+)$/.exec(overId);
  if (!am || !om) return null;
  const number = Number(am[1]);
  const overNumber = Number(om[1]);
  if (number === overNumber) return null;
  const over = entities.find((e) => e.number === overNumber);
  if (!over) return null;
  const status = fieldText(over.fields[groupField]);
  const patch: Record<string, unknown> = { [groupField]: status };
  if (!sortActive) {
    // Reorder within the target column: rank between `over` and the card above it.
    const column = entities
      .filter((e) => fieldText(e.fields[groupField]) === status)
      .sort((a, b) => rankOf(a) - rankOf(b) || a.number - b.number);
    const rank = rankForDrop(column, number, overNumber);
    if (rank != null) patch.rank = rank;
  }
  return { number, patch };
}

/** @dnd-kit `onDragEnd` → the single write path. Column-only callers can omit
 * `entities`/`sortActive` (a status-change drop needs neither); passing them
 * enables card-onto-card manual reorder (#GH-projects P4). */
export function handleDragEnd(
  event: { active: { id: string | number }; over: { id: string | number } | null },
  groupField: string,
  onPatch: (number: number, patch: Record<string, unknown>) => void,
  entities: EntityInstance[] = [],
  sortActive = false,
): void {
  const result = dropResult(
    String(event.active.id),
    event.over ? String(event.over.id) : null,
    groupField,
    entities,
    sortActive,
  );
  if (result) onPatch(result.number, result.patch);
}
