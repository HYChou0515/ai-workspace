/**
 * #847 PR 3 P2: a chart's rows ↔ a named marking.
 *
 * A marking is the picked rows' values on its keys (#861 D1) — the SDK's
 * `Marking`. Reading: a layer row is lit by the PLATFORM's matching rule
 * (`isLit`, from the SDK — never a second copy here, or two views on one
 * marking would disagree), over the marking's keys that layer carries: every
 * key, exact membership; some, whether it contains a pick (D2). Writing: a
 * selection, or the spec's `highlight:` on open, writes each picked row's
 * `keys:` values as one pick (`markingFrom`, the SDK's one constructor).
 */
import { isLit, type Marking, markingFrom, markingSize } from "@aiws/view-sdk";

import { litRows } from "./highlight";
import type { Answer, Measured } from "./option";
import { keyColumn, type Selection, selectionRows } from "./selection";
import { canon } from "./wire";

const NONE_MEASURED: ReadonlySet<string> = new Set();

/** Per layer, which rows the marking lights; null for a layer that carries none
 * of the marking's keys (drawn undimmed). The keys compared are the
 * marking's own that the layer carries — so a view with no `keys:` is still
 * lit on a same-named column (Q6); `keys:` decides only what a view WRITES.
 * A column a channel aggregates (`measured`, PR 5 P32) is not carried: it
 * holds the aggregate, not the field's values. An aggregated element lights
 * whole when it contains a pick (#861 D4 — the projection rule, D2). */
export function markingLit(answer: Answer, marking: Marking, measured: Measured): (boolean[] | null)[] {
  return answer.layers.map((layer, i) => {
    // Through `keyColumn`, the ONE place a key's marking strings are read — the
    // lookup `selectionRows` writes with. A key a channel sends as time or
    // numbers carries its strings in `$key.<name>`; decoding the channel's own
    // column compared "1704153600000" against a written "2024-01-02".
    //
    // Without `$key.<name>` (the sandbox sends it only for THIS view's `keys:`),
    // a `time` or `q8` channel column holds epoch ms / quantized codes, never a
    // marking's strings — so it is not compared at all, and the layer draws
    // undimmed rather than all-dim as "no match". A view links on a time column
    // by naming it in `keys:` [#856 review, mine — open to override].
    const cols = marking.keys.flatMap((k) => {
      const plain = layer.columns[k];
      if (!layer.columns[`$key.${k}`] && plain && (plain.kind === "time" || plain.kind === "q8")) {
        return [];
      }
      const col = keyColumn(layer, k, measured[i] ?? NONE_MEASURED);
      return col ? [[k, col] as const] : [];
    });
    if (cols.length === 0) return null;
    return Array.from({ length: layer.rows }, (_, r) => {
      const row: Record<string, string> = {};
      for (const [k, col] of cols) {
        const v = canon(col.value(r));
        if (v !== null) row[k] = v;
      }
      return isLit(row, marking);
    });
  });
}

function sameMarking(a: Marking, b: Marking): boolean {
  return (
    a.keys.length === b.keys.length &&
    a.keys.every((k, i) => k === b.keys[i]) &&
    a.tuples.size === b.tuples.size &&
    [...b.tuples].every((t) => a.tuples.has(t))
  );
}

/** Whether a marking `entry` still holds exactly what a view wrote (`wrote`,
 * written as `source`) -- by the host store's own test for "the same write
 * again" (same source, same keys, same picks; `marking.test.ts` holds this to
 * it). An empty write is still held while the marking holds nothing
 * (#847/#848 PR 5 P34). */
export function stillWritten(
  entry: { readonly marking: Marking; readonly source: string | null } | undefined,
  wrote: Marking,
  source: string | null,
): boolean {
  if (markingSize(wrote) === 0) return !entry;
  return !!entry && entry.source === source && sameMarking(entry.marking, wrote);
}

/** The picks of `selections`, one per selected row that gives every key of
 * the pick a value.
 *
 * The keys of the pick are the `keys:` columns every naming selection gave a
 * value -- a selection whose rows name no key (a binned layer: its rows are
 * bins; a layer carrying none of the keys, or only as a column a channel
 * aggregates, PR 5 P32/P41 row 21; rows whose keys are all empty, P44 row 35)
 * takes no part. A layer that names fewer keys (a stack's segment, summed over
 * the field it does not link by) makes the pick coarser, never a guess at the
 * values it does not have [#861, mine — open to override]. null when nothing
 * is named: a selection that names nothing marks nothing, never the empty
 * marking that would clear every linked view (the table's rule). */
function picks(
  selections: readonly Selection[],
  answer: Answer,
  keys: readonly string[],
  measured: Measured,
): Marking | null {
  const naming = selections
    .map((s) => selectionRows(s, answer, keys, measured))
    .map((rows) => ({ rows, named: new Set(rows.flatMap((r) => Object.keys(r))) }))
    .filter((s) => s.named.size > 0);
  if (naming.length === 0) return null;
  const common = keys.filter((k) => naming.every((s) => s.named.has(k)));
  // layers naming disjoint keys share no pick
  if (common.length === 0) return null;
  const rows = naming.flatMap((s) => s.rows).flatMap((row) => {
    const values = common.map((k) => row[k]);
    return values.every((v): v is string => v !== undefined) ? [values] : [];
  });
  const marking = markingFrom(common, rows);
  return markingSize(marking) > 0 ? marking : null;
}

/** What a selection writes. null for a view without `keys:` (it cannot be a
 * source), and for a selection that names no pick (`picks`). The empty
 * marking only for an empty selection — the write that clears. */
export function selectionMarking(
  selections: readonly Selection[],
  answer: Answer,
  keys: string[],
  measured: Measured,
): Marking | null {
  if (keys.length === 0) return null;
  if (selections.length === 0) return markingFrom(keys, []);
  return picks(selections, answer, keys, measured);
}

/** How many picks a selection wrote to the marking (#861 D5): the count said
 * beside "by <keys>", as every view on the marking counts. Rows that name no
 * pick write nothing and are not counted (PR 5 P43, P44 row 35); two rows with
 * the same key values are one pick. */
export function markedCount(
  selections: readonly Selection[],
  answer: Answer,
  keys: string[],
  measured: Measured,
): number {
  const m = keys.length === 0 ? null : picks(selections, answer, keys, measured);
  return m ? markingSize(m) : 0;
}

/** The spec's `highlight:` as a marking — what seeds an empty marking on open,
 * resolved by the sandbox exactly as PR 2 draws it. null when no layer carries
 * a highlight, the view has no `keys:`, or the lit rows name no pick. */
export function highlightMarking(answer: Answer, keys: string[], measured: Measured): Marking | null {
  if (keys.length === 0) return null;
  const selections: Selection[] = [];
  answer.layers.forEach((layer, i) => {
    const lit = litRows(layer);
    if (lit) selections.push({ source: "brush", layer: i, rows: lit.flatMap((on, r) => (on ? [r] : [])) });
  });
  if (selections.length === 0) return null;
  return picks(selections, answer, keys, measured);
}
