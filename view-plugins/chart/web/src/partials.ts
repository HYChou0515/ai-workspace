/**
 * #861 D3: the picked part of an aggregated bar.
 *
 * The sandbox's `partials` command answers, per drawn bar, the decomposable
 * partials of its rows grouped by the marking's keys (`chart_view.partials`):
 * each a key tuple and its rows' n, sum, min and max. The browser folds the
 * ones the marking lights on every marking change -- asking again only when
 * the marking's KEYS change -- into the bar's value over the picked rows
 * only: count 10 with 2 picked is a lit bar of 2; a mean is the picked rows'
 * mean. `wire-corpus/bar-partials.json` holds this fold to the sandbox's own
 * aggregate over the picked rows (`partials.corpus.test.ts`).
 */
import { isLit, type Marking } from "@aiws/view-sdk";

/** One partial: [key tuple index, n, sum, min, max]. */
export type Partial = [number, number, number, number | null, number | null];

/** A layer's partials: null when the layer is not split (lit whole), a reason
 * when it could have been and is not (lit whole, said in the note line). */
export type LayerPartials =
  | null
  | { whole: string }
  | { by: string[]; op: string; keys: string[][]; bars: Partial[][] };

export type PartialsAnswer = { format: 1; layers: LayerPartials[] };

/** The sandbox's answer, or null for one this renderer cannot read (drawn
 * lit whole, as with no partials). */
export function readPartials(stdout: string): PartialsAnswer | null {
  try {
    const parsed = JSON.parse(stdout) as PartialsAnswer;
    return parsed.format === 1 && Array.isArray(parsed.layers) ? parsed : null;
  } catch {
    return null;
  }
}

/** The op's value over `entries` (the picked ones of a bar); null for none
 * picked, and where the op has no value (a mean of no numbers). */
export function foldPartials(op: string, entries: readonly Partial[]): number | null {
  if (entries.length === 0) return null;
  let n = 0;
  let sum = 0;
  let lo: number | null = null;
  let hi: number | null = null;
  for (const [, count, s, min, max] of entries) {
    n += count;
    sum += s;
    if (min !== null && (lo === null || min < lo)) lo = min;
    if (max !== null && (hi === null || max > hi)) hi = max;
  }
  switch (op) {
    case "count":
      return n;
    case "sum":
      return sum;
    case "min":
      return lo;
    case "max":
      return hi;
    default: // mean, rate: the answer holds only the ops that fold
      return n > 0 ? sum / n : null;
  }
}

/** Whether the partials were made for the marking's keys: ones asked for an
 * earlier key set (the next answer still on its way) split by the wrong
 * columns, and are not used. */
export function forKeys(p: LayerPartials, marking: Marking): p is Exclude<LayerPartials, null | { whole: string }> {
  return !!p && "by" in p && p.by.length === marking.keys.length && p.by.every((k, i) => k === marking.keys[i]);
}

/** Per drawn bar, its value over the picked rows (null: none picked), by the
 * platform's `isLit` on each partial's key tuple; null when the partials are
 * not for this marking's keys. */
export function litValues(p: LayerPartials, marking: Marking): (number | null)[] | null {
  if (!forKeys(p, marking)) return null;
  const lit = p.keys.map((tuple) => isLit(Object.fromEntries(p.by.map((k, i) => [k, tuple[i]!])), marking));
  return p.bars.map((entries) => foldPartials(p.op, entries.filter((e) => lit[e[0]])));
}
