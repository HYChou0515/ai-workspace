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
import type { Answer, Measured } from "./option";
import { keyColumn, type Selection, selectionValues } from "./selection";
import { canon } from "./wire";

/** `column → values`, as the SDK's `Marking`. Declared structurally so this
 * pure module needs no runtime SDK import. */
export type MarkingValues = { readonly [column: string]: ReadonlySet<string> };
const NONE_MEASURED: ReadonlySet<string> = new Set();

export type IsLit = (row: Readonly<Record<string, string>>, marking: MarkingValues) => boolean;

/** Per layer, which rows the marking lights; null for a layer that carries none
 * of the marking's columns (drawn undimmed). The columns compared are the
 * marking's own that the layer carries — so a view with no `keys:` is still
 * lit on a same-named column (Q6); `keys:` decides only what a view WRITES.
 * A column a channel aggregates (`measured`, PR 5 P32) is not carried: it
 * holds the aggregate, not the field's values. */
export function markingLit(answer: Answer, marking: MarkingValues, lit: IsLit, measured: Measured): (boolean[] | null)[] {
  return answer.layers.map((layer, i) => {
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

/** Whether a marking `entry` still holds exactly what a view wrote (`wrote`,
 * written as `source`) -- by the host store's own test for "the same write
 * again" (same source, same columns, same values; `marking.test.ts` holds
 * this to it). An empty write is still held while the marking holds nothing
 * (#847/#848 PR 5 P34). */
export function stillWritten(
  entry: { readonly marking: MarkingValues; readonly source: string | null } | undefined,
  wrote: MarkingValues,
  source: string | null,
): boolean {
  const cols = Object.keys(wrote).filter((c) => (wrote[c] as ReadonlySet<string>).size > 0);
  if (!entry) return cols.length === 0;
  if (entry.source !== source || cols.length !== Object.keys(entry.marking).length) return false;
  return cols.every((c) => {
    const held = entry.marking[c];
    const want = wrote[c] as ReadonlySet<string>;
    return held !== undefined && held.size === want.size && [...want].every((v) => held.has(v));
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
 * source), and for a selection whose layers can name no key -- binned ones
 * (their rows are bins), and any carrying none of the keys, or only as a
 * column a channel aggregates (#847/#848 PR 5 P41 row 21): a selection that
 * names nothing marks nothing, never the `{}` that would clear every linked
 * view (the table's rule). `{}` only for an empty selection — the write that
 * clears. */
export function selectionMarking(
  selections: readonly Selection[],
  answer: Answer,
  keys: string[],
  measured: Measured,
): MarkingValues | null {
  if (keys.length === 0) return null;
  const named = selections.map((s) => selectionValues(s, answer, keys, measured)).filter((v) => Object.keys(v).length > 0);
  if (selections.length > 0 && named.length === 0) return null;
  return toMarking(named);
}

/** How many of a selection's rows went to the marking: the rows that gave a
 * key a value (#847/#848 PR 5 P43, P44 row 35). A stack beside an unstacked
 * layer writes nothing for a field it does not link by (P42 row 29), so
 * counting its segment beside "by <columns>" said "7 selected · by item" for
 * 6 items; a row whose keys are all empty writes nothing either. */
export function markedCount(
  selections: readonly Selection[],
  answer: Answer,
  keys: string[],
  measured: Measured,
): number {
  let n = 0;
  for (const s of selections) {
    const cols = keys.flatMap((k) => keyColumn(answer.layers[s.layer], k, measured[s.layer] ?? NONE_MEASURED) ?? []);
    for (const r of s.rows) if (cols.some((col) => canon(col.value(r)) !== null)) n += 1;
  }
  return n;
}

/** The spec's `highlight:` as a marking — what seeds an empty marking on open,
 * resolved by the sandbox exactly as PR 2 draws it. null when no layer carries
 * a highlight, or the view has no `keys:`. */
export function highlightMarking(answer: Answer, keys: string[], measured: Measured): MarkingValues | null {
  if (keys.length === 0) return null;
  const selections: Selection[] = [];
  answer.layers.forEach((layer, i) => {
    const lit = litRows(layer);
    if (lit) selections.push({ source: "brush", layer: i, rows: lit.flatMap((on, r) => (on ? [r] : [])) });
  });
  if (selections.length === 0) return null;
  return toMarking(selections.map((s) => selectionValues(s, answer, keys, measured)));
}
