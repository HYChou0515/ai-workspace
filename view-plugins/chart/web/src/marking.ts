/**
 * #847 PR 3 P2: a chart's rows ↔ a named marking.
 *
 * A marking is `column → set of values` (canon strings); which columns link is
 * the spec's `keys:`. Reading: a layer row is lit by the PLATFORM's matching
 * rule (`isLit`, from the SDK — never a second copy here, or two views on one
 * marking would disagree), over the columns that layer carries. Writing: a
 * selection, or the spec's `highlight:` on open, is projected onto `keys:`.
 */
import { litRows } from "./highlight";
import type { Answer } from "./option";
import { type Selection, selectionValues } from "./selection";
import { canon, decodeColumn } from "./wire";

/** `column → values`, as the SDK's `Marking`. Declared structurally so this
 * pure module needs no runtime SDK import. */
export type MarkingValues = { readonly [column: string]: ReadonlySet<string> };
export type IsLit = (row: Readonly<Record<string, string>>, marking: MarkingValues) => boolean;

/** Per layer, which rows the marking lights; null for a layer that carries none
 * of the marking's columns (drawn undimmed). The columns compared are the
 * marking's own that the layer carries — so a view with no `keys:` is still
 * lit on a same-named column (Q6); `keys:` decides only what a view WRITES. */
export function markingLit(answer: Answer, marking: MarkingValues, lit: IsLit): (boolean[] | null)[] {
  return answer.layers.map((layer) => {
    const present = Object.keys(marking).filter((k) => layer.columns[k] !== undefined);
    if (present.length === 0) return null;
    const cols = present.map((k) => [k, decodeColumn(layer.columns[k]!)] as const);
    return Array.from({ length: layer.rows }, (_, r) => {
      const row: Record<string, string> = {};
      for (const [k, col] of cols) {
        const v = canon(col.value(r));
        if (v !== null) row[k] = v;
      }
      return lit(row, marking);
    });
  });
}

function toMarking(values: Record<string, string[]>[]): MarkingValues {
  const out: Record<string, Set<string>> = {};
  for (const v of values) {
    for (const [k, list] of Object.entries(v)) {
      if (list.length === 0) continue;
      const set = (out[k] ??= new Set());
      for (const x of list) set.add(x);
    }
  }
  return out;
}

/** What a selection writes. null for a view without `keys:` (it cannot be a
 * source); `{}` for an empty selection — the write that clears. */
export function selectionMarking(
  selections: readonly Selection[],
  answer: Answer,
  keys: string[],
): MarkingValues | null {
  if (keys.length === 0) return null;
  return toMarking(selections.map((s) => selectionValues(s, answer, keys)));
}

/** The spec's `highlight:` as a marking — what seeds an empty marking on open,
 * resolved by the sandbox exactly as PR 2 draws it. null when no layer carries
 * a highlight, or the view has no `keys:`. */
export function highlightMarking(answer: Answer, keys: string[]): MarkingValues | null {
  if (keys.length === 0) return null;
  const selections: Selection[] = [];
  answer.layers.forEach((layer, i) => {
    const lit = litRows(layer);
    if (lit) selections.push({ source: "brush", layer: i, rows: lit.flatMap((on, r) => (on ? [r] : [])) });
  });
  if (selections.length === 0) return null;
  return toMarking(selections.map((s) => selectionValues(s, answer, keys)));
}
