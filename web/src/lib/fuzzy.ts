/**
 * Small fuzzy matcher for the "go to file" palette — subsequence match with a
 * relevance score, so `wafmap` finds `wafer_map.csv` and the best matches sort
 * first. Deliberately hand-rolled (the codebase carries no fuzzy dependency).
 *
 * Slash-normalized on purpose: a stored path can carry U+2215 (`∕`, DIVISION
 * SLASH) where an ASCII `/` would have gone — specstar ids can't hold `/`, so
 * that look-alike is the convention (kb/doc_id.py, filestore/specstar_impl.py).
 * Nobody can type `∕`, so a typed `/` must match it: both the query and the
 * candidate fold every slash variant to a plain `/` before matching.
 */

/** Every character that reads as a path separator, folded to ASCII `/`. */
const SLASHES = /[∕⁄／]/g; // U+2215 division, U+2044 fraction, U+FF0F fullwidth

function norm(s: string): string {
  return s.toLowerCase().replace(SLASHES, "/");
}

/** Characters after which a match is "at a word boundary" and scores higher. */
function isBoundary(ch: string): boolean {
  return ch === "/" || ch === "_" || ch === "-" || ch === "." || ch === " ";
}

/**
 * A relevance score for `query` against `text`, or `null` when `text` does not
 * contain `query`'s characters in order. Higher is a better match. Boundaries
 * (start of the name, after `/ _ - .`) and runs of consecutive matches score
 * highest, so `map` ranks `map.csv` over `wafer_map.csv` over `m_a_p.txt`.
 */
export function fuzzyScore(query: string, text: string): number | null {
  const q = norm(query);
  const t = norm(text);
  if (q === "") return 0;

  let score = 0;
  let qi = 0;
  let prevMatch = -2; // index of the previous matched char in `t`
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] !== q[qi]) continue;
    let here = 1;
    // Contiguity dominates: a run of consecutive matches (`map` in `map.csv`)
    // must beat the same letters scattered across boundaries (`m_a_p`), which is
    // what a person typing `map` actually wants.
    if (ti === prevMatch + 1) here += 10; // consecutive with the previous match
    if (ti === 0 || isBoundary(t[ti - 1])) here += 5; // at a word boundary
    score += here;
    prevMatch = ti;
    qi++;
  }
  if (qi < q.length) return null; // ran out of text before matching every char
  // Shorter candidates are tighter matches for the same query.
  return score - t.length * 0.1;
}

/**
 * Keep the items whose `key` fuzzy-matches `query`, best match first. An empty
 * query keeps every item in its original order. Ties keep input order (stable).
 */
export function fuzzyFilter<T>(query: string, items: readonly T[], key: (item: T) => string): T[] {
  if (query.trim() === "") return [...items];
  const scored: { item: T; score: number; i: number }[] = [];
  items.forEach((item, i) => {
    const score = fuzzyScore(query, key(item));
    if (score !== null) scored.push({ item, score, i });
  });
  scored.sort((a, b) => b.score - a.score || a.i - b.i);
  return scored.map((s) => s.item);
}
