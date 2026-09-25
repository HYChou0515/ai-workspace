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
import { keyColumn, type Selection, selectionValues } from "./selection";
import { canon } from "./wire";

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
    // Through `keyColumn`, the ONE place a key's marking strings are read — the
    // lookup `selectionValues` writes with. A key a channel sends as time or
    // numbers carries its strings in `$key.<name>`; decoding the channel's own
    // column compared "1704153600000" against a written "2024-01-02".
    //
    // Without `$key.<name>` (the sandbox sends it only for THIS view's `keys:`),
    // a `time` or `q8` channel column holds epoch ms / quantized codes, never a
    // marking's strings — so it is not compared at all, and the layer draws
    // undimmed rather than all-dim as "no match". A view links on a time column
    // by naming it in `keys:` [#856 review, mine — open to override].
    const cols = Object.keys(marking).flatMap((k) => {
      const plain = layer.columns[k];
      if (!layer.columns[`$key.${k}`] && plain && (plain.kind === "time" || plain.kind === "q8")) {
        return [];
      }
      const col = keyColumn(layer, k);
      return col ? [[k, col] as const] : [];
    });
    if (cols.length === 0) return null;
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

/** "by group, item": the columns a marking marks by, said beside a count
 * (#847/#848 PR 5 P27) — the words the host's tables say (`markedBy` in the
 * host's markings module; `marking.test.ts` holds this to it). */
export function markedBy(marking: MarkingValues): string {
  return `by ${Object.keys(marking).join(", ")}`;
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
 * source), and for a selection made only on binned layers (their rows are
 * bins, which name no row); `{}` for an empty selection — the write that clears. */
export function selectionMarking(
  selections: readonly Selection[],
  answer: Answer,
  keys: string[],
): MarkingValues | null {
  if (keys.length === 0) return null;
  const onRows = selections.filter((s) => !answer.layers[s.layer]?.binned);
  if (selections.length > 0 && onRows.length === 0) return null;
  return toMarking(onRows.map((s) => selectionValues(s, answer, keys)));
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
